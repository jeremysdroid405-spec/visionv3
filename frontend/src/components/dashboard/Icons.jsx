import React, { useState, memo } from 'react';
import { Zap } from 'lucide-react';

// THE DEMON - Fierce devil face with sharp horns
export const DemonIcon = memo(({ size = 24, className = '', isScanning = false, hasVision = false }) => (
  <div className={`demon-icon-container ${isScanning ? 'demon-scanning' : ''} ${className}`} style={{ width: size, height: size }}>
    <svg 
      width={size} 
      height={size} 
      viewBox="0 0 24 24" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg"
      className="demon-icon"
    >
      {/* Left horn - sharp and curved */}
      <path 
        d="M6 12L3 2L10 9" 
        fill="#DC2626"
        stroke="#7F1D1D" 
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Right horn - sharp and curved */}
      <path 
        d="M18 12L21 2L14 9" 
        fill="#DC2626"
        stroke="#7F1D1D" 
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Face - angular and aggressive */}
      <path 
        d="M12 6L6 12L8 20H16L18 12L12 6Z" 
        fill="#1C1917"
        stroke="#DC2626" 
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Left eye - slanted angry */}
      <path 
        d="M8 11L11 13L8 14Z" 
        fill="#EF4444"
      />
      {/* Right eye - slanted angry */}
      <path 
        d="M16 11L13 13L16 14Z" 
        fill="#EF4444"
      />
      {/* Angry eyebrow lines */}
      <path 
        d="M7 10L10 12" 
        stroke="#EF4444" 
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path 
        d="M17 10L14 12" 
        stroke="#EF4444" 
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      {/* Jagged mouth with fangs */}
      <path 
        d="M9 17L10 16L11 17L12 15L13 17L14 16L15 17" 
        stroke="#EF4444" 
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
    {hasVision && <div className="vision-sparkle" />}
  </div>
));

DemonIcon.displayName = 'DemonIcon';

// THE GOBLIN - Sinister hooded figure with glowing eyes
export const GoblinIcon = memo(({ size = 24, className = '', isClicked = false, hasVision = false }) => {
  const [clicked, setClicked] = useState(false);
  
  const handleClick = (e) => {
    e.stopPropagation();
    setClicked(true);
    setTimeout(() => setClicked(false), 500);
  };
  
  return (
    <div 
      className={`goblin-icon-container ${clicked || isClicked ? 'goblin-pulse' : ''} ${className}`} 
      style={{ width: size, height: size }}
      onClick={handleClick}
    >
      <svg 
        width={size} 
        height={size} 
        viewBox="0 0 24 24" 
        fill="none" 
        xmlns="http://www.w3.org/2000/svg"
        className="goblin-icon"
      >
        {/* Hood/cloak */}
        <path 
          d="M4 20L6 8C6 8 8 4 12 4C16 4 18 8 18 8L20 20L12 18L4 20Z" 
          fill="#0D3320"
          stroke="#16A34A" 
          strokeWidth="1"
        />
        {/* Pointed ear left */}
        <path 
          d="M5 12L1 6L7 10" 
          fill="#166534"
          stroke="#22C55E" 
          strokeWidth="1"
          strokeLinejoin="round"
        />
        {/* Pointed ear right */}
        <path 
          d="M19 12L23 6L17 10" 
          fill="#166534"
          stroke="#22C55E" 
          strokeWidth="1"
          strokeLinejoin="round"
        />
        {/* Dark face */}
        <ellipse 
          cx="12" 
          cy="12" 
          rx="5" 
          ry="6" 
          fill="#052E16"
        />
        {/* Glowing slit eyes */}
        <path 
          d="M8 11L11 12L8 13" 
          fill="#4ADE80"
          stroke="#22C55E"
          strokeWidth="0.5"
        />
        <path 
          d="M16 11L13 12L16 13" 
          fill="#4ADE80"
          stroke="#22C55E"
          strokeWidth="0.5"
        />
        {/* Eye glow effect */}
        <circle cx="9.5" cy="12" r="1" fill="#4ADE80" opacity="0.5"/>
        <circle cx="14.5" cy="12" r="1" fill="#4ADE80" opacity="0.5"/>
        {/* Sinister grin */}
        <path 
          d="M9 15C10 16.5 14 16.5 15 15" 
          stroke="#22C55E" 
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        {/* Sharp teeth hint */}
        <path 
          d="M10 15.5L10.5 16.5M14 15.5L13.5 16.5" 
          stroke="#4ADE80" 
          strokeWidth="0.75"
          strokeLinecap="round"
        />
      </svg>
      {hasVision && <div className="vision-sparkle vision-sparkle-green" />}
    </div>
  );
});

GoblinIcon.displayName = 'GoblinIcon';

// Vision Synergy Badge - For Master Tier cards
export const VisionBadge = memo(({ type = 'demon', hasVision = false }) => {
  if (!hasVision) return null;
  
  return (
    <div className={`
      absolute -top-2 -right-2 w-8 h-8 rounded-full 
      flex items-center justify-center z-10
      ${type === 'demon' 
        ? 'bg-gradient-to-br from-red-600 to-red-900 shadow-lg shadow-red-500/50' 
        : 'bg-gradient-to-br from-green-600 to-green-900 shadow-lg shadow-green-500/50'
      }
    `}>
      <Zap className="w-4 h-4 text-white" />
    </div>
  );
});

VisionBadge.displayName = 'VisionBadge';
