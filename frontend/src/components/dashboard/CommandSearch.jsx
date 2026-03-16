/**
 * COMMAND SEARCH COMPONENT
 * =========================
 * Global player search for Command Post.
 * 
 * TODO: Subscribe to Global Store for [Player Search Results]
 * PURGED: All localized fetch() calls removed
 */

import React, { useState, useCallback, memo } from 'react';
import { Search, X, User, Loader2 } from 'lucide-react';
import { Input } from '../ui/input';
import debounce from 'lodash/debounce';

// PURGED: API_URL constant removed - no direct API calls from components
// const API_URL = process.env.REACT_APP_BACKEND_URL;

const CommandSearch = memo(({ onPlayerSelect, placeholder = "Search players..." }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);

  // TODO: Subscribe to Global Store for [Player Search]
  // PURGED: Debounced search function - localized fetch removed
  const searchPlayers = useCallback(
    debounce((searchQuery) => {
      if (searchQuery.length < 2) {
        setResults([]);
        return;
      }

      setLoading(true);
      // TODO: Dispatch action to Global Store to search players
      console.log('[COMMAND SEARCH] Search fetch purged - awaiting Global Store:', searchQuery);
      
      // Temporary: simulate loading then show empty
      setTimeout(() => {
        setLoading(false);
        setResults([]);  // Results will come from Global Store subscription
      }, 300);
    }, 300),
    []
  );

  const handleInputChange = (e) => {
    const value = e.target.value;
    setQuery(value);
    setShowResults(true);
    searchPlayers(value);
  };

  const handleSelectPlayer = (player) => {
    setQuery('');
    setResults([]);
    setShowResults(false);
    onPlayerSelect?.(player);
  };

  const handleClear = () => {
    setQuery('');
    setResults([]);
    setShowResults(false);
  };

  return (
    <div className="relative" data-testid="command-search">
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
                  <div className="w-8 h-8 rounded-full bg-zinc-700 flex items-center justify-center">
                    <User className="w-4 h-4 text-zinc-400" />
                  </div>
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

      {/* Click outside to close */}
      {showResults && (
        <div 
          className="fixed inset-0 z-40" 
          onClick={() => setShowResults(false)}
        />
      )}
    </div>
  );
});

CommandSearch.displayName = 'CommandSearch';

export default CommandSearch;
