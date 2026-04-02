/**
 * MomentumToggle.jsx
 * ===================
 * Global toggle for switching between Season-only and Composite defensive ranking modes.
 * 
 * When in Season-only mode, the Ferrari Power Score uses only the season-long DvP rank.
 * When in Composite mode, it uses the weighted formula: 50% Season + 35% L10 + 15% L5.
 */

import React, { memo } from 'react';
import { Shield, Info } from 'lucide-react';

const MomentumToggle = memo(({ 
  useComposite, 
  onToggle,
  compact = false 
}) => {
  if (compact) {
    return (
      <button
        onClick={onToggle}
        className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium transition-all ${
          useComposite 
            ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30'
            : 'bg-zinc-700/50 text-zinc-400 border border-zinc-600/50 hover:bg-zinc-700'
        }`}
        title={useComposite ? 'Using Composite DvP (50% Season + 35% L10 + 15% L5)' : 'Using Season-only DvP'}
        data-testid="momentum-toggle-compact"
      >
        <Shield className="w-3 h-3" />
        {useComposite ? 'Composite' : 'Season'}
      </button>
    );
  }

  return (
    <div 
      className="flex items-center gap-3 px-3 py-2 bg-zinc-800/50 rounded-lg border border-zinc-700/50"
      data-testid="momentum-toggle-full"
    >
      <div className="flex items-center gap-2">
        <Shield className="w-4 h-4 text-cyan-400" />
        <span className="text-xs text-zinc-400">Defensive Mode</span>
      </div>
      
      {/* Toggle Switch */}
      <div className="flex items-center gap-1 bg-zinc-900 rounded-full p-0.5">
        <button
          onClick={() => !useComposite && onToggle()}
          className={`px-2.5 py-1 rounded-full text-[10px] font-medium transition-all ${
            !useComposite 
              ? 'bg-zinc-600 text-white'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          Season
        </button>
        <button
          onClick={() => useComposite && onToggle()}
          className={`px-2.5 py-1 rounded-full text-[10px] font-medium transition-all ${
            useComposite 
              ? 'bg-cyan-600 text-white'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          Composite
        </button>
      </div>
      
      {/* Info tooltip trigger */}
      <div className="relative group">
        <Info className="w-3.5 h-3.5 text-zinc-500 cursor-help" />
        <div className="absolute bottom-full right-0 mb-2 p-2 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 w-48">
          <div className="text-[10px] text-zinc-300 leading-relaxed">
            <span className="text-cyan-400 font-medium">Composite:</span> 50% Season + 35% L10 + 15% L5
            <br />
            <span className="text-zinc-400 font-medium">Season:</span> Full season average only
          </div>
        </div>
      </div>
    </div>
  );
});

MomentumToggle.displayName = 'MomentumToggle';

export default MomentumToggle;
