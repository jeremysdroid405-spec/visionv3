/**
 * GLOBAL QUERY PROVIDER
 * =====================
 * TanStack Query (React Query) configuration for SSOT Two-Pipe Architecture
 * 
 * ARCHITECTURE:
 * ┌─────────────────────────────────────────────────────────────────────────────┐
 * │                    TANSTACK QUERY GLOBAL STATE                              │
 * ├─────────────────────────────────────────────────────────────────────────────┤
 * │                                                                             │
 * │  PIPE 1: useMasterStats(playerId)                                          │
 * │  ├─ Source: /api/v3/master-hub/player/{playerId}                           │
 * │  ├─ staleTime: 24 hours (data only changes at 0400 EST CRON)              │
 * │  └─ Cache: Heavy - never refetch in same session                           │
 * │                                                                             │
 * │  PIPE 2: useLiveOdds()                                                     │
 * │  ├─ Source: /api/v3/cached-props (Active Lines)                            │
 * │  ├─ refetchInterval: 30 seconds (Open Door polling)                        │
 * │  └─ Cache: Light - fresh odds are critical                                 │
 * │                                                                             │
 * │  INTERSECTION: Components merge Pipe 1 + Pipe 2 via playerId              │
 * │                                                                             │
 * └─────────────────────────────────────────────────────────────────────────────┘
 */

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// QueryClient with SSOT-optimized defaults
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Prevent aggressive refetching - our data is CRON-scheduled
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      
      // Default retry behavior
      retry: 1,
      retryDelay: 1000,
      
      // Default stale time (5 minutes for most queries)
      staleTime: 5 * 60 * 1000,
      
      // Keep data in cache for 30 minutes
      gcTime: 30 * 60 * 1000,
    },
  },
});

/**
 * GlobalQueryProvider - Wraps app with TanStack Query context
 */
export const GlobalQueryProvider = ({ children }) => {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
};

// Export queryClient for manual cache operations (invalidation, prefetch)
export { queryClient };
