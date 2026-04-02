/**
 * useMomentumPreferences.js
 * ==========================
 * Hook for managing user preferences for the Defensive Momentum feature.
 * 
 * Stores preferences in localStorage for persistence across sessions.
 * 
 * Preferences:
 * - useComposite: boolean - Whether to use composite scoring (default: true)
 *                          false = Season-only mode
 */

import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'propvision_momentum_prefs';

const DEFAULT_PREFS = {
  useComposite: true, // true = Composite mode, false = Season-only mode
};

export const useMomentumPreferences = () => {
  const [prefs, setPrefs] = useState(() => {
    // Initialize from localStorage
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        return { ...DEFAULT_PREFS, ...JSON.parse(stored) };
      }
    } catch (e) {
      console.error('[MomentumPrefs] Error loading preferences:', e);
    }
    return DEFAULT_PREFS;
  });

  // Persist to localStorage when prefs change
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch (e) {
      console.error('[MomentumPrefs] Error saving preferences:', e);
    }
  }, [prefs]);

  const setUseComposite = useCallback((value) => {
    setPrefs(prev => ({ ...prev, useComposite: value }));
  }, []);

  const toggleMode = useCallback(() => {
    setPrefs(prev => ({ ...prev, useComposite: !prev.useComposite }));
  }, []);

  return {
    useComposite: prefs.useComposite,
    setUseComposite,
    toggleMode,
    // Expose the full prefs object
    prefs,
    // Helper labels
    modeLabel: prefs.useComposite ? 'Composite' : 'Season Only',
  };
};

export default useMomentumPreferences;
