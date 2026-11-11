"""Automated API integration and endpoint tests for Demerivo PixelFix."""

import io
import os
import shutil
import tempfile
import unittest
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import sessionmaker

from demerivo.api.routers.health import root_health, api_health, storage_health
from demerivo.api.routers.uploads import (
    create_presigned_upload,
    upload_image_direct,
    PresignedUploadRequest,
)
from demerivo.api.routers.jobs import (
    create_job,
    get_job_status,
    cancel_job,
    get_job_download_url,
    delete_job,
)
from demerivo.config import Settings
from demerivo.db.session import Base, create_db_engine
from demerivo.db.models import JobStatus, OperationType, TenantModel, TenantRole, UserModel, WalletModel
from demerivo.jobs.schemas import JobCreate
from demerivo.jobs.service import JobService
from demerivo.jobs.dispatcher import JobDispatcher
from demerivo.storage.local import LocalStorage


def create_test_png() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAPIEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

        self.test_settings = Settings(
            env="testing",
            debug=True,
            storage_provider="local",
            local_storage_dir=self.temp_dir,
            database_url=f"sqlite:///{self.db_path}",
        )

        self.engine = create_db_engine(self.test_settings.database_url)
        Base.metadata.create_all(bind=self.engine)
        self.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.TestingSessionLocal()

        # Seed authenticated user, personal tenant, and wallet
        self.user = UserModel(id="usr-api-test", email="api_test@demerivo.com", display_name="API Tester", is_active=True)
        self.tenant = TenantModel(id="tnt-api-test", name="Tester Workspace", slug="tester-ws", owner_id=self.user.id)
        self.wallet = WalletModel(id="wlt-api-test", tenant_id=self.tenant.id, balance=50)
        self.db.add_all([self.user, self.tenant, self.wallet])
        self.db.commit()

        self.tenant_context = (self.tenant, TenantRole.OWNER)

        self.storage = LocalStorage(base_dir=self.temp_dir)
        from demerivo.workers.manager import WorkerManager
        self.worker_manager = WorkerManager(self.test_settings, session_factory=self.TestingSessionLocal, storage=self.storage)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_health_endpoints(self):
        # 1. Root health
        h = root_health()
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["service"], "demerivo-pixelfix")

        # 2. Detailed API health
        api_h = api_health(settings=self.test_settings, db=self.db)
        self.assertEqual(api_h["status"], "ok")
        self.assertEqual(api_h["database"], "healthy")
        self.assertEqual(api_h["environment"], "testing")

        # 3. Storage health
        s_h = storage_health(settings=self.test_settings, storage=self.storage)
        self.assertEqual(s_h["status"], "healthy")
        self.assertEqual(s_h["storage_provider"], "local")

    def test_capabilities_endpoint(self):
        from demerivo.api.routers.jobs import get_capabilities
        caps = get_capabilities()
        self.assertGreaterEqual(len(caps), 7)
        op_types = [c.operation_type for c in caps]
        self.assertIn("upscale_2x", op_types)
        self.assertIn("upscale_4x", op_types)
        self.assertIn("bg_remove", op_types)
        self.assertIn("enhance", op_types)

    def test_presigned_upload_endpoint(self):
        # Valid request
        req = PresignedUploadRequest(
            filename="headshot.jpg",
            content_type="image/jpeg",
            file_size_bytes=1024 * 500,
        )
        resp = create_presigned_upload(
            req=req,
            current_user=self.user,
            tenant_context=self.tenant_context,
            settings=self.test_settings,
            storage=self.storage,
        )
        self.assertIsNotNone(resp.upload_url)
        self.assertTrue(resp.object_key.startswith(f"uploads/{self.tenant.id}/"))
        self.assertTrue(resp.object_key.endswith(".jpg"))
        self.assertEqual(resp.method, "PUT")

        # Invalid extension
        with self.assertRaises(HTTPException) as ctx:
            bad_ext = PresignedUploadRequest(
                filename="script.sh",
                content_type="image/jpeg",
                file_size_bytes=1000
            )
            create_presigned_upload(
                bad_ext,
                current_user=self.user,
                tenant_context=self.tenant_context,
                settings=self.test_settings,
                storage=self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Unsupported file extension", ctx.exception.detail)

        # Invalid MIME
        with self.assertRaises(HTTPException) as ctx:
            bad_mime = PresignedUploadRequest(
                filename="valid.jpg",
                content_type="application/x-executable",
                file_size_bytes=1000
            )
            create_presigned_upload(
                bad_mime,
                current_user=self.user,
                tenant_context=self.tenant_context,
                settings=self.test_settings,
                storage=self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Unsupported MIME type", ctx.exception.detail)

        # Oversized file
        with self.assertRaises(HTTPException) as ctx:
            oversized = PresignedUploadRequest(
                filename="huge.jpg",
                content_type="image/jpeg",
                file_size_bytes=self.test_settings.max_upload_size_bytes + 1024
            )
            create_presigned_upload(
                oversized,
                current_user=self.user,
                tenant_context=self.tenant_context,
                settings=self.test_settings,
                storage=self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("exceeds maximum allowed limit", ctx.exception.detail)

    def test_direct_image_upload_and_job_lifecycle(self):
        # 1. Direct file upload
        png_data = create_test_png()
        file = UploadFile(filename="photo.png", file=io.BytesIO(png_data))
        upload_resp = upload_image_direct(
            file=file,
            current_user=self.user,
            tenant_context=self.tenant_context,
            settings=self.test_settings,
            storage=self.storage,
        )
        self.assertEqual(upload_resp.format, "PNG")
        self.assertEqual(upload_resp.width, 64)
        self.assertEqual(upload_resp.height, 64)
        input_key = upload_resp.object_key
        self.assertTrue(self.storage.exists(input_key))

        # 2. Submit processing job (upscale 4x = 4 credits)
        job_req = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key=input_key,
            parameters={"face_restore": False}
        )
        job_resp = create_job(
            request=job_req,
            current_user=self.user,
            tenant_context=self.tenant_context,
            db=self.db,
            storage=self.storage,
            worker_manager=self.worker_manager,
        )
        job_id = job_resp.id
        self.assertEqual(job_resp.operation_type, OperationType.UPSCALE_4X)
        self.assertEqual(job_resp.input_key, input_key)
        self.assertEqual(job_resp.credit_cost, 4)

        # 3. Retrieve status (initially queued by dispatcher)
        status_resp = get_job_status(job_id=job_id, tenant_context=self.tenant_context, db=self.db)
        self.assertEqual(status_resp.id, job_id)
        self.assertEqual(status_resp.status, JobStatus.QUEUED)

        # 4. Job download must fail with 409 if not completed
        with self.assertRaises(HTTPException) as ctx:
            get_job_download_url(job_id=job_id, tenant_context=self.tenant_context, db=self.db, storage=self.storage)
        self.assertEqual(ctx.exception.status_code, 409)

        # 5. Simulate transition to PROCESSING then COMPLETED
        output_key = f"outputs/{self.tenant.id}/{job_id}.png"
        self.storage.upload_file(key=output_key, data=png_data, content_type="image/png")
        JobService.update_status(self.db, job_id, JobStatus.PROCESSING, progress=50, tenant_id=self.tenant.id)
        JobService.update_status(self.db, job_id, JobStatus.COMPLETED, progress=100, output_key=output_key, tenant_id=self.tenant.id)

        # Now download succeeds
        dl_resp = get_job_download_url(job_id=job_id, tenant_context=self.tenant_context, db=self.db, storage=self.storage)
        self.assertEqual(dl_resp.job_id, job_id)
        self.assertIn("/api/v1/storage/", dl_resp.download_url)

        # 6. Delete job
        delete_job(job_id=job_id, tenant_context=self.tenant_context, db=self.db, storage=self.storage)
        self.assertFalse(self.storage.exists(input_key))
        self.assertFalse(self.storage.exists(output_key))

        # Verify job is gone
        with self.assertRaises(HTTPException) as ctx:
            get_job_status(job_id=job_id, tenant_context=self.tenant_context, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_direct_upload_invalid_payload(self):
        fake_file = UploadFile(filename="corrupted.png", file=io.BytesIO(b"Not an image at all"))
        with self.assertRaises(HTTPException) as ctx:
            upload_image_direct(
                file=fake_file,
                current_user=self.user,
                tenant_context=self.tenant_context,
                settings=self.test_settings,
                storage=self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("unrecognized image binary signature", ctx.exception.detail)

    def test_job_not_found(self):
        with self.assertRaises(HTTPException) as ctx:
            get_job_status("non-existent-uuid-12345", tenant_context=self.tenant_context, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancel_job_endpoint(self):
        req = JobCreate(operation_type=OperationType.ENHANCE, input_key="dummy.png")
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)
        res = cancel_job(job.id, current_user=self.user, tenant_context=self.tenant_context, db=self.db)
        self.assertEqual(res.status, JobStatus.CANCELLED)
        self.assertIn("cancelled", res.message)


if __name__ == "__main__":
    unittest.main()
