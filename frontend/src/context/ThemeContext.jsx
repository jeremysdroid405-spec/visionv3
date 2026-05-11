/**
 * ThemeContext — global light / dark theme selector.
 *
 * Persists the user's preference in localStorage (`pv:theme`) and
 * applies the canonical `dark` class to `<html>` so Tailwind's
 * `darkMode: ['class']` mode + the light-mode CSS overlay in
 * `index.css` (`:root:not(.dark) ...`) flips the palette.
 *
 * Default is `dark` — every existing component is dark-tuned, so an
 * unset preference must NOT regress users to the light overlay.
 */
import React, {
  createContext, useContext, useState, useEffect, useCallback, useMemo,
} from 'react';

const ThemeContext = createContext(null);
const STORAGE_KEY = 'pv:theme';
const VALID = new Set(['dark', 'light']);

const readInitial = () => {
  if (typeof window === 'undefined') return 'dark';
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (VALID.has(stored)) return stored;
  } catch { /* localStorage unavailable */ }
  return 'dark';
};

const applyThemeClass = (theme) => {
  if (typeof document === 'undefined') return;
  const html = document.documentElement;
  if (theme === 'dark') {
    html.classList.add('dark');
    html.setAttribute('data-theme', 'dark');
  } else {
    html.classList.remove('dark');
    html.setAttribute('data-theme', 'light');
  }
};

export const ThemeProvider = ({ children }) => {
  const [theme, setThemeState] = useState(readInitial);

  // Apply the class on mount + on every theme change.
  useEffect(() => {
    applyThemeClass(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch { /* ignore */ }
  }, [theme]);

  const setTheme = useCallback((next) => {
    if (!VALID.has(next)) return;
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme, isDark: theme === 'dark' }),
    [theme, setTheme, toggleTheme],
  );

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = () => {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    // Soft-fail: returning a stub keeps components that mount before
    // the provider (e.g. during SSR / test harness) from crashing.
    return { theme: 'dark', setTheme: () => {}, toggleTheme: () => {}, isDark: true };
  }
  return ctx;
};

export default ThemeContext;
