import React, { useState, useRef, useCallback } from 'react';

interface BeforeAfterSliderProps {
  beforeImage: string;
  afterImage: string;
  beforeLabel?: string;
  afterLabel?: string;
  aspectRatio?: string;
}

export const BeforeAfterSlider: React.FC<BeforeAfterSliderProps> = ({
  beforeImage,
  afterImage,
  beforeLabel = 'Original',
  afterLabel = 'Enhanced',
  aspectRatio = '4/3',
}) => {
  const [sliderPosition, setSliderPosition] = useState(50);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMove = useCallback((clientX: number) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const percent = Math.max(0, Math.min(100, (x / rect.width) * 100));
    setSliderPosition(percent);
  }, []);

  const handleTouchMove = (e: React.TouchEvent) => {
    if (isDragging && e.touches[0]) {
      handleMove(e.touches[0].clientX);
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      handleMove(e.clientX);
    }
  };

  return (
    <div
      ref={containerRef}
      className="slider-container"
      style={{ aspectRatio }}
      onMouseDown={() => setIsDragging(true)}
      onMouseUp={() => setIsDragging(false)}
      onMouseLeave={() => setIsDragging(false)}
      onMouseMove={handleMouseMove}
      onTouchStart={() => setIsDragging(true)}
      onTouchEnd={() => setIsDragging(false)}
      onTouchMove={handleTouchMove}
    >
      {/* Enhanced / After Image (Underneath) */}
      <img
        src={afterImage}
        alt="After enhancement"
        className="slider-image-after"
        draggable={false}
      />
      <div
        style={{
          position: 'absolute',
          top: '1rem',
          right: '1rem',
          background: 'rgba(16, 22, 38, 0.85)',
          color: '#A5B4FC',
          padding: '0.25rem 0.75rem',
          borderRadius: '9999px',
          fontSize: '0.75rem',
          fontWeight: 700,
          border: '1px solid rgba(99, 102, 241, 0.3)',
          zIndex: 2,
        }}
      >
        {afterLabel}
      </div>

      {/* Original / Before Image (Clipped overlay) */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: `${sliderPosition}%`,
          height: '100%',
          overflow: 'hidden',
          zIndex: 3,
        }}
      >
        <img
          src={beforeImage}
          alt="Before enhancement"
          className="slider-image-before"
          style={{ width: containerRef.current ? `${containerRef.current.offsetWidth}px` : '100%' }}
          draggable={false}
        />
        <div
          style={{
            position: 'absolute',
            top: '1rem',
            left: '1rem',
            background: 'rgba(16, 22, 38, 0.85)',
            color: '#94A3B8',
            padding: '0.25rem 0.75rem',
            borderRadius: '9999px',
            fontSize: '0.75rem',
            fontWeight: 700,
            border: '1px solid #1E293B',
          }}
        >
          {beforeLabel}
        </div>
      </div>

      {/* Dividing Divider Line & Thumb */}
      <div
        className="slider-handle"
        style={{ left: `${sliderPosition}%`, zIndex: 4 }}
      >
        <div className="slider-handle-button">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6"></polyline>
            <polyline points="9 18 15 12 9 6" style={{ transform: 'rotate(180deg)', transformOrigin: 'center' }}></polyline>
          </svg>
        </div>
      </div>
    </div>
  );
};
