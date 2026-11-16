"""Automated tests for JobService and lifecycle state transitions."""

import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from demerivo.db.models import JobModel, JobStatus, OperationType, TenantModel, UserModel, WalletModel
from demerivo.db.session import Base
from demerivo.jobs.schemas import JobCreate
from demerivo.jobs.service import (
    InvalidJobStateTransition,
    JobNotFoundError,
    JobService,
)


class TestJobLifecycle(unittest.TestCase):
    def setUp(self):
        # In-memory SQLite for isolated test runs
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed test user, workspace, and wallet with credits
        self.user = UserModel(id="usr-test-lifecycle", email="lifecycle@test.com", is_active=True)
        self.tenant = TenantModel(id="tnt-test-lifecycle", name="Lifecycle WS", slug="lifecycle-ws", owner_id=self.user.id)
        self.wallet = WalletModel(id="wlt-test-lifecycle", tenant_id=self.tenant.id, balance=50)
        self.db.add_all([self.user, self.tenant, self.wallet])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_job_creation(self):
        req = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key="uploads/photo.jpg",
            parameters={"face_restore": True}
        )
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)

        self.assertIsNotNone(job.id)
        self.assertEqual(job.status, JobStatus.PENDING)
        self.assertEqual(job.operation_type, OperationType.UPSCALE_4X)
        self.assertEqual(job.input_key, "uploads/photo.jpg")
        self.assertEqual(job.parameters, {"face_restore": True})
        self.assertEqual(job.progress, 0)
        self.assertIsNone(job.output_key)

    def test_valid_lifecycle_transitions(self):
        req = JobCreate(operation_type=OperationType.BG_REMOVE, input_key="in.png")
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)

        # Pending -> Queued
        job = JobService.update_status(self.db, job.id, JobStatus.QUEUED, progress=10, tenant_id=self.tenant.id)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.progress, 10)

        # Queued -> Processing
        job = JobService.update_status(self.db, job.id, JobStatus.PROCESSING, progress=50, tenant_id=self.tenant.id)
        self.assertEqual(job.status, JobStatus.PROCESSING)
        self.assertEqual(job.progress, 50)

        # Processing -> Completed
        job = JobService.update_status(
            self.db, job.id, JobStatus.COMPLETED, progress=100, output_key="out.png", tenant_id=self.tenant.id
        )
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.output_key, "out.png")
        self.assertIsNotNone(job.completed_at)

    def test_invalid_lifecycle_transition_raises(self):
        req = JobCreate(operation_type=OperationType.DENOISE, input_key="in.png")
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)

        # Mark completed
        JobService.update_status(self.db, job.id, JobStatus.PROCESSING, tenant_id=self.tenant.id)
        JobService.update_status(self.db, job.id, JobStatus.COMPLETED, tenant_id=self.tenant.id)

        # Attempting to move from terminal COMPLETED to PROCESSING must fail
        with self.assertRaises(InvalidJobStateTransition):
            JobService.update_status(self.db, job.id, JobStatus.PROCESSING, tenant_id=self.tenant.id)

    def test_job_cancellation(self):
        req = JobCreate(operation_type=OperationType.ENHANCE, input_key="in.png")
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)
        JobService.update_status(self.db, job.id, JobStatus.PROCESSING, tenant_id=self.tenant.id)

        cancelled_job = JobService.cancel_job(self.db, job.id, tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(cancelled_job.status, JobStatus.CANCELLED)
        self.assertIn("cancelled", cancelled_job.error_message.lower())

    def test_job_not_found(self):
        with self.assertRaises(JobNotFoundError):
            JobService.get_job(self.db, "non-existent-uuid-999", tenant_id=self.tenant.id)

    def test_job_deletion(self):
        req = JobCreate(operation_type=OperationType.COLORIZE, input_key="in.png")
        job = JobService.create_job(self.db, req, tenant_id=self.tenant.id, user_id=self.user.id)
        job_id = job.id

        deleted = JobService.delete_job(self.db, job_id, tenant_id=self.tenant.id)
        self.assertTrue(deleted)

        with self.assertRaises(JobNotFoundError):
            JobService.get_job(self.db, job_id, tenant_id=self.tenant.id)


if __name__ == "__main__":
    unittest.main()
