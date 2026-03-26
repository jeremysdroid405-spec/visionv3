/**
 * COMMAND SEARCH COMPONENT
 * =========================
 * Global player search for Command Post.
 * 
 * SSOT: Uses usePlayerSearch hook (PIPE 2) for search results
 */

import React, { useState, useCallback, memo, useEffect } from 'react';
import { Search, X, User, Loader2 } from 'lucide-react';
import { Input } from '../ui/input';
import debounce from 'lodash/debounce';

// SSOT Global State Hooks
import { usePlayerSearch } from '../../hooks/useLiveOdds';

const CommandSearch = memo(({ onPlayerSelect, placeholder = "Search players..." }) => {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [showResults, setShowResults] = useState(false);
  
  // Ref to track if we're clicking inside the component
  const containerRef = React.useRef(null);

  // Debounce the query for API calls
  const debouncedSetQuery = useCallback(
    debounce((q) => setDebouncedQuery(q), 300),
    []
  );
  
  // Update debounced query when query changes
  useEffect(() => {
    debouncedSetQuery(query);
  }, [query, debouncedSetQuery]);
  
  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setShowResults(false);
      }
    };
    
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // PIPE 2: Search players via usePlayerSearch hook
  const { data: searchData, isLoading: loading } = usePlayerSearch(debouncedQuery);
  const results = searchData?.players || [];

  const handleInputChange = (e) => {
    const value = e.target.value;
    setQuery(value);
    setShowResults(true);
  };

  const handleSelectPlayer = (player) => {
    setQuery('');
    setDebouncedQuery('');
    setShowResults(false);
    onPlayerSelect?.(player);
  };

  const handleClear = () => {
    setQuery('');
    setDebouncedQuery('');
    setShowResults(false);
  };

  return (
    <div className="relative" data-testid="command-search" ref={containerRef}>
      {/* Search Input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
        <Input
          type="text"
          value={query}
          onChange={handleInputChange}
          onFocus={() => setShowResults(true)}
          placeholder={placeholder}
          className="pl-10 pr-10 bg-zinc-900/50 border-zinc-700 focus:border-cyan-500 text-white placeholder-zinc-500"
          data-testid="command-search-input"
        />
        {query && (
          <button
            onClick={handleClear}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Results Dropdown */}
      {showResults && (query.length >= 2 || results.length > 0) && (
        <div 
          className="absolute z-50 w-full mt-1 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl overflow-hidden"
          data-testid="command-search-results"
        >
          {loading ? (
            <div className="flex items-center justify-center p-4">
              <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" />
              <span className="ml-2 text-sm text-zinc-400">Searching...</span>
            </div>
          ) : results.length > 0 ? (
            <ul className="max-h-64 overflow-y-auto">
              {results.map((player) => (
                <li
                  key={player.id}
                  onClick={() => handleSelectPlayer(player)}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-zinc-800 cursor-pointer border-b border-zinc-800 last:border-0 transition-colors"
                  data-testid={`search-result-${player.id}`}
                >
                  {/* Player Photo - Use photo_url (SSOT from master hub) */}
                  {(player.photo_url || player.headshot_url) ? (
                    <div className="w-8 h-8 rounded-full overflow-hidden bg-zinc-700">
                      <img 
                        src={player.photo_url || player.headshot_url} 
                        alt={player.player_name}
                        className="w-full h-full object-cover"
                        style={{ objectPosition: 'center 20%', transform: 'scale(1.3)' }}
                        onError={(e) => e.target.parentElement.innerHTML = `<div class="w-full h-full flex items-center justify-center"><span class="text-zinc-400 text-xs font-bold">${player.player_name?.charAt(0) || '?'}</span></div>`}
                      />
                    </div>
                  ) : (
                    <div className="w-8 h-8 rounded-full bg-zinc-700 flex items-center justify-center">
                      <User className="w-4 h-4 text-zinc-400" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white truncate">
                      {player.player_name}
                    </p>
                    <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                      <span className="font-mono">{player.team || '---'}</span>
                      {player.position && (
                        <>
                          <span>·</span>
                          <span>{player.position}</span>
                        </>
                      )}
                      {player.has_stats && (
                        <span className="px-1 py-0.5 bg-emerald-500/20 text-emerald-400 rounded text-[8px]">STATS</span>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : query.length >= 2 ? (
            <div className="p-4 text-center text-sm text-zinc-500">
              No players found for "{query}"
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
});

CommandSearch.displayName = 'CommandSearch';

export default CommandSearch;
