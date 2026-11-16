import React, { useState } from 'react';
import { ProcessingJob } from '../types';

interface ImageHistoryProps {
  jobs: ProcessingJob[];
  onOpenStudio: () => void;
}

export const ImageHistory: React.FC<ImageHistoryProps> = ({ jobs, onOpenStudio }) => {
  const [filterOp, setFilterOp] = useState<string>('all');

  const filteredJobs = filterOp === 'all'
    ? jobs
    : jobs.filter((j) => j.operation_type === filterOp);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '2rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.25rem' }}>
            My Processed Images
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
            Browse and download your high-resolution enhanced assets.
          </p>
        </div>

        {/* Filter Dropdown */}
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <select
            value={filterOp}
            onChange={(e) => setFilterOp(e.target.value)}
            style={{
              padding: '0.5rem 1rem',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              fontSize: '0.85rem',
            }}
          >
            <option value="all">All Operations</option>
            <option value="upscale_2x">2x Upscale</option>
            <option value="upscale_4x">4x Upscale</option>
            <option value="bg_remove">Background Removal</option>
            <option value="enhance">Enhance</option>
            <option value="denoise">Denoise</option>
            <option value="deblur">Deblur</option>
            <option value="colorize">Colorize</option>
          </select>

          <button className="btn btn-primary" onClick={onOpenStudio} style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}>
            New Enhancement
          </button>
        </div>
      </div>

      {filteredJobs.length === 0 ? (
        <div className="glass-panel" style={{ textAlign: 'center', padding: '4rem 1.5rem', color: 'var(--text-secondary)' }}>
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ margin: '0 auto 1rem', opacity: 0.5 }}>
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <circle cx="8.5" cy="8.5" r="1.5"/>
            <polyline points="21 15 16 10 5 21"/>
          </svg>
          <h3 style={{ fontSize: '1.2rem', fontWeight: 700, marginBottom: '0.5rem' }}>No images found</h3>
          <p style={{ fontSize: '0.9rem', maxWidth: '380px', margin: '0 auto 1.5rem' }}>
            {filterOp === 'all'
              ? 'You have not processed any photos yet. Visit the AI Studio to start.'
              : `No images match the filter '${filterOp}'.`}
          </p>
          <button className="btn btn-primary" onClick={onOpenStudio}>
            Open AI Studio
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1.5rem' }}>
          {filteredJobs.map((job) => (
            <div
              key={job.id}
              className="glass-panel"
              style={{
                overflow: 'hidden',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
              }}
            >
              {/* Image Preview Box */}
              <div
                style={{
                  width: '100%',
                  aspectRatio: '4/3',
                  background: '#000',
                  position: 'relative',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {job.output_key ? (
                  <img
                    src={job.result_url || `/api/v1/jobs/${job.id}/download`}
                    alt="Processed"
                    style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                    loading="lazy"
                  />
                ) : (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    {job.status === 'processing' ? 'Processing...' : 'No Preview'}
                  </div>
                )}
                <div style={{ position: 'absolute', top: '0.75rem', right: '0.75rem' }}>
                  <span className={`badge ${job.status === 'completed' ? 'badge-success' : 'badge-primary'}`}>
                    {job.status.toUpperCase()}
                  </span>
                </div>
              </div>

              {/* Details and Actions */}
              <div style={{ padding: '1.25rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <span style={{ fontWeight: 700, fontSize: '1rem' }}>
                    {job.operation_type.toUpperCase()}
                  </span>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {job.credit_cost} Credits
                  </span>
                </div>

                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                  Created: {new Date(job.created_at).toLocaleString()}
                </div>

                {job.status === 'completed' && job.output_key && (
                  <a
                    href={job.result_url || `/api/v1/jobs/${job.id}/download`}
                    download={`demerivo_${job.id.slice(0, 8)}.png`}
                    className="btn btn-secondary"
                    style={{ width: '100%', textAlign: 'center', fontSize: '0.85rem' }}
                  >
                    Download Image
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
