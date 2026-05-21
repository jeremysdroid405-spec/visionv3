import React from 'react';
import '@/App.css';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { SportProvider } from './context/SportContext';
import { ThemeProvider } from './context/ThemeContext';
import { GlobalQueryProvider } from './providers/QueryProvider';
import { ProtectedRoute } from './components/ProtectedRoute';
import { Auth } from './pages/Auth';
import Dashboard from './pages/Dashboard';
import NBADashboard from './pages/NBADashboard';
import MLBDashboard from './pages/MLBDashboard';
import AdminPRAAudit from './pages/AdminPRAAudit';
import AdminIdentityStatus from './pages/AdminIdentityStatus';
import AdminTesting from './pages/AdminTesting';
import { Toaster } from 'sonner';

// Demo mode wrappers - passes isDemoMode prop to dashboards
const DemoModeWrapper = () => <Dashboard isDemoMode={true} />;
const NBADemoWrapper = () => <NBADashboard isDemoMode={true} />;
const MLBDemoWrapper = () => <MLBDashboard isDemoMode={true} />;

function App() {
  return (
    <GlobalQueryProvider>
      <AuthProvider>
        <ThemeProvider>
          <SportProvider>
            <div className="App">
            <Toaster 
              position="top-right" 
              theme="dark"
              toastOptions={{
                style: {
                  background: '#18181B',
                  color: '#FAFAFA',
                  border: '1px solid #27272A',
                },
              }}
            />
            <BrowserRouter>
              <Routes>
                {/* Auth page - public landing page */}
                <Route path="/" element={<Auth />} />
                <Route path="/auth" element={<Auth />} />
                
                {/* Protected routes - require login */}
                <Route 
                  path="/dashboard" 
                  element={
                    <ProtectedRoute>
                      <Dashboard />
                    </ProtectedRoute>
                  } 
                />
                
                {/* Sport-specific routes - NBA */}
                <Route 
                  path="/nba" 
                  element={
                    <ProtectedRoute>
                      <NBADashboard />
                    </ProtectedRoute>
                  } 
                />
                
                {/* Sport-specific routes - MLB */}
                <Route 
                  path="/mlb" 
                  element={
                    <ProtectedRoute>
                      <MLBDashboard />
                    </ProtectedRoute>
                  } 
                />
                
                {/* Legacy routes - redirect to main dashboard */}
                <Route path="/v3" element={<Navigate to="/dashboard" replace />} />
                <Route path="/v3-legacy" element={<Navigate to="/dashboard" replace />} />
                <Route path="/v4/demo" element={<Navigate to="/demo" replace />} />
                <Route path="/full-board" element={<Navigate to="/dashboard" replace />} />
                
                {/* Admin — PRA dual-projection audit (token-protected via X-Admin-Token) */}
                <Route path="/admin/pra-audit" element={<AdminPRAAudit />} />

                {/* Admin — Global Identity Rule observability (token-protected) */}
                <Route path="/admin/identity-status" element={<AdminIdentityStatus />} />

                {/* Admin — Private Universal Historical Testing Command Center
                    (unlinked, token-protected via X-Admin-Token) */}
                <Route path="/admin/testing" element={<AdminTesting />} />

                {/* Demo pages - public for testing */}
                <Route path="/demo" element={<DemoModeWrapper />} />
                <Route path="/v3/demo" element={<DemoModeWrapper />} />
                <Route path="/demo/nba" element={<NBADemoWrapper />} />
                <Route path="/demo/mlb" element={<MLBDemoWrapper />} />
              </Routes>
            </BrowserRouter>
          </div>
        </SportProvider>
      </ThemeProvider>
      </AuthProvider>
    </GlobalQueryProvider>
  );
}

export default App;
