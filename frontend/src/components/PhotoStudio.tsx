import React, { useState, useRef, useEffect } from 'react';
import { api } from '../api';
import { AICapability, OperationType, ProcessingJob } from '../types';
import { BeforeAfterSlider } from './BeforeAfterSlider';

interface PhotoStudioProps {
  walletBalance: number;
  onRefreshWallet: () => void;
  onJobCompleted: (job: ProcessingJob) => void;
}

export const PhotoStudio: React.FC<PhotoStudioProps> = ({
  walletBalance,
  onRefreshWallet,
  onJobCompleted,
}) => {
  const [capabilities, setCapabilities] = useState<AICapability[]>([]);
  const [selectedOp, setSelectedOp] = useState<OperationType>('upscale_2x');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [uploadedKey, setUploadedKey] = useState<string | null>(null);

  // Operation specific parameters
  const [bgColor, setBgColor] = useState<string>('#FFFFFF');
  const [isSolidBg, setIsSolidBg] = useState<boolean>(false);

  // Processing state
  const [uploading, setUploading] = useState<boolean>(false);
  const [processing, setProcessing] = useState<boolean>(false);
  const [activeJob, setActiveJob] = useState<ProcessingJob | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch capabilities from backend
  useEffect(() => {
    api.getCapabilities()
      .then((caps) => {
        // Filter only commercially cleared models
        const cleared = caps.filter((c: any) => c.commercial_cleared || c.commercial_license_cleared);
        setCapabilities(cleared);
      })
      .catch((err) => console.error('Failed to load capabilities:', err));
  }, []);

  // Poll active job status until completed or failed
  useEffect(() => {
    let interval: any = null;
    if (processing && activeJob && activeJob.status !== 'completed' && activeJob.status !== 'failed') {
      interval = setInterval(async () => {
        try {
          const updated = await api.getJob(activeJob.id);
          setActiveJob(updated);

          if (updated.status === 'completed') {
            setProcessing(false);
            clearInterval(interval);
            onRefreshWallet();
            onJobCompleted(updated);
            if (updated.result_url) {
              setResultUrl(updated.result_url);
            } else {
              try {
                const downloadUrl = await api.getJobDownloadUrl(updated.id);
                setResultUrl(downloadUrl);
              } catch (e) {
                console.error('Failed to fetch presigned download URL:', e);
              }
            }
          } else if (updated.status === 'failed') {
            setProcessing(false);
            clearInterval(interval);
            setError(updated.error_message || 'Image processing failed. Credits have been refunded.');
            onRefreshWallet();
          }
        } catch (err: any) {
          console.error('Error polling job:', err);
        }
      }, 1000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [processing, activeJob, onRefreshWallet, onJobCompleted]);

  const handleFileSelect = async (file: File) => {
    setError(null);
    setResultUrl(null);
    setActiveJob(null);

    // Validate size (20MB)
    if (file.size > 20 * 1024 * 1024) {
      setError('File size exceeds the 20 MB limit. Please select a smaller photo.');
      return;
    }

    // Validate extension
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff'].includes(ext)) {
      setError('Unsupported file type. Please upload a JPG, PNG, or WebP image.');
      return;
    }

    setSelectedFile(file);
    const localUrl = URL.createObjectURL(file);
    setPreviewUrl(localUrl);

    // Upload to storage
    setUploading(true);
    try {
      const { file_key } = await api.uploadImage(file);
      setUploadedKey(file_key);
    } catch (err: any) {
      setError(err.message || 'Failed to upload photo to storage.');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelect(e.dataTransfer.files[0]);
    }
  };

  const handleStartProcessing = async () => {
    if (!uploadedKey) return;
    setError(null);

    const activeCap = capabilities.find((c) => c.operation_type === selectedOp);
    const cost = activeCap ? activeCap.credit_cost : 2;

    if (walletBalance < cost) {
      setError(`Insufficient credit balance. This operation requires ${cost} credits, but you have ${walletBalance}.`);
      return;
    }

    setProcessing(true);
    try {
      const params: Record<string, any> = {};
      if (selectedOp === 'bg_remove' && isSolidBg) {
        params['bg_color'] = bgColor;
      }

      const job = await api.createJob(selectedOp, uploadedKey, params);
      setActiveJob(job);
      onRefreshWallet();
    } catch (err: any) {
      setProcessing(false);
      setError(err.message || 'Failed to initiate processing job.');
    }
  };

  const activeCap = capabilities.find((c) => c.operation_type === selectedOp);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Studio Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '2rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.25rem' }}>
            AI Photo Studio
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
            High-precision super-resolution, background isolation, and color restoration.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span className="badge badge-primary">
            Balance: {walletBalance} Credits
          </span>
          {activeCap && (
            <span className="badge badge-success">
              Cost: {activeCap.credit_cost} Credits
            </span>
          )}
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: '1rem',
            borderRadius: 'var(--radius-md)',
            background: 'var(--danger-bg)',
            color: 'var(--danger)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            fontSize: '0.9rem',
          }}
        >
          {error}
        </div>
      )}

      {/* Main Studio Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '2rem' }}>
        {/* Left Column: Operation Selection & Controls */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {/* Operation Selector Panel */}
          <div className="glass-panel" style={{ padding: '1.75rem' }}>
            <h3 style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: '1.25rem' }}>
              1. Choose AI Transformation
            </h3>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.75rem' }}>
              {capabilities.map((cap) => {
                const isSelected = selectedOp === cap.operation_type;
                return (
                  <button
                    key={cap.operation_type}
                    type="button"
                    onClick={() => setSelectedOp(cap.operation_type)}
                    style={{
                      padding: '0.85rem 1rem',
                      borderRadius: 'var(--radius-md)',
                      background: isSelected ? 'var(--primary-glow)' : 'var(--bg-card)',
                      border: isSelected ? '2px solid #818CF8' : '1px solid var(--border-subtle)',
                      color: isSelected ? '#FFFFFF' : 'var(--text-secondary)',
                      textAlign: 'left',
                      cursor: 'pointer',
                      transition: 'all var(--transition-fast)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                      <span style={{ fontWeight: 700, fontSize: '0.9rem', color: isSelected ? '#A5B4FC' : 'var(--text-primary)' }}>
                        {cap.display_name}
                      </span>
                      <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#818CF8' }}>
                        {cap.credit_cost}C
                      </span>
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.3 }}>
                      {cap.description.slice(0, 50)}...
                    </div>
                  </button>
                );
              })}
            </div>

            {/* Optional Parameters (e.g. background color replacement) */}
            {selectedOp === 'bg_remove' && (
              <div style={{ marginTop: '1.5rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border-subtle)' }}>
                <h4 style={{ fontSize: '0.9rem', fontWeight: 700, marginBottom: '0.75rem' }}>Background Options</h4>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.75rem' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="bgType"
                      checked={!isSolidBg}
                      onChange={() => setIsSolidBg(false)}
                    />
                    Transparent PNG (Alpha)
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="bgType"
                      checked={isSolidBg}
                      onChange={() => setIsSolidBg(true)}
                    />
                    Solid Color Backdrop
                  </label>
                </div>

                {isSolidBg && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <input
                      type="color"
                      value={bgColor}
                      onChange={(e) => setBgColor(e.target.value)}
                      style={{ width: '40px', height: '40px', borderRadius: 'var(--radius-sm)', border: 'none', cursor: 'pointer' }}
                    />
                    <span style={{ fontSize: '0.85rem', fontFamily: 'var(--font-mono)' }}>{bgColor}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Action Button */}
          <button
            className="btn btn-primary"
            style={{ padding: '1rem', fontSize: '1.05rem', fontWeight: 700 }}
            disabled={!uploadedKey || uploading || processing}
            onClick={handleStartProcessing}
          >
            {uploading
              ? 'Uploading Photo...'
              : processing
              ? `Processing (${activeJob?.progress || 10}%)...`
              : !uploadedKey
              ? 'Please Select a Photo First'
              : `Enhance Image (${activeCap?.credit_cost || 2} Credits)`}
          </button>
        </div>

        {/* Right Column: Upload Dropzone & Interactive Results Viewer */}
        <div className="glass-panel" style={{ padding: '1.75rem', display: 'flex', flexDirection: 'column' }}>
          <h3 style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: '1.25rem' }}>
            2. Preview & Result Comparison
          </h3>

          {!previewUrl ? (
            /* Drag and Drop Zone */
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              style={{
                flex: 1,
                minHeight: '360px',
                border: '2px dashed var(--border-strong)',
                borderRadius: 'var(--radius-lg)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '2.5rem',
                textAlign: 'center',
                cursor: 'pointer',
                background: 'rgba(16, 22, 38, 0.4)',
                transition: 'border-color var(--transition-fast)',
              }}
            >
              <div
                style={{
                  width: '4rem',
                  height: '4rem',
                  borderRadius: '50%',
                  background: 'var(--bg-card)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: '1rem',
                  color: '#818CF8',
                }}
              >
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
              </div>
              <h4 style={{ fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.5rem' }}>
                Drag and drop your photo here
              </h4>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', maxWidth: '320px', marginBottom: '1.25rem' }}>
                Supports JPG, PNG, WebP, BMP, and TIFF files up to 20 MB.
              </p>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.85rem' }}
                onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
              >
                Browse Files
              </button>
            </div>
          ) : resultUrl ? (
            /* Before / After Interactive Comparison Slider */
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <BeforeAfterSlider
                beforeImage={previewUrl}
                afterImage={resultUrl}
                beforeLabel="Original Upload"
                afterLabel={`Enhanced (${selectedOp.toUpperCase()})`}
                aspectRatio="4/3"
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
                <button
                  className="btn btn-outline"
                  onClick={() => {
                    setPreviewUrl(null);
                    setSelectedFile(null);
                    setResultUrl(null);
                    setUploadedKey(null);
                  }}
                >
                  Upload Another Photo
                </button>
                <a
                  href={resultUrl}
                  download={`demerivo_enhanced_${Date.now()}.png`}
                  className="btn btn-primary"
                  style={{ textDecoration: 'none' }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                  Download High-Res Result
                </a>
              </div>
            </div>
          ) : (
            /* Single Image Preview While Processing */
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div
                style={{
                  position: 'relative',
                  width: '100%',
                  aspectRatio: '4/3',
                  borderRadius: 'var(--radius-lg)',
                  overflow: 'hidden',
                  background: '#000',
                }}
              >
                <img
                  src={previewUrl}
                  alt="Upload Preview"
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                />

                {processing && (
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      background: 'rgba(9, 13, 22, 0.8)',
                      backdropFilter: 'blur(4px)',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      padding: '2rem',
                      textAlign: 'center',
                    }}
                  >
                    <div
                      style={{
                        width: '48px',
                        height: '48px',
                        border: '3px solid rgba(99, 102, 241, 0.3)',
                        borderTopColor: '#6366F1',
                        borderRadius: '50%',
                        animation: 'spin 1s linear infinite',
                        marginBottom: '1rem',
                      }}
                    />
                    <h4 style={{ fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.25rem' }}>
                      AI Inference in Progress...
                    </h4>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: '1rem' }}>
                      Running {selectedOp.toUpperCase()} on host CPU cluster
                    </p>
                    {/* Animated Progress Bar */}
                    <div style={{ width: '100%', maxWidth: '280px', height: '8px', background: 'var(--bg-card)', borderRadius: '9999px', overflow: 'hidden' }}>
                      <div
                        style={{
                          width: `${activeJob?.progress || 15}%`,
                          height: '100%',
                          background: 'linear-gradient(90deg, #6366F1, #8B5CF6)',
                          transition: 'width 0.3s ease',
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {!processing && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    {selectedFile?.name} ({(selectedFile?.size ? selectedFile.size / 1024 : 0).toFixed(0)} KB)
                  </span>
                  <button
                    className="btn btn-outline"
                    style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                    onClick={() => {
                      setPreviewUrl(null);
                      setSelectedFile(null);
                      setUploadedKey(null);
                    }}
                  >
                    Change Image
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Hidden HTML5 File Input for Mobile & Desktop Pickers */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files && e.target.files[0]) {
                handleFileSelect(e.target.files[0]);
              }
            }}
          />
        </div>
      </div>
    </div>
  );
};
