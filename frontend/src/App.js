import React from 'react';
import '@/App.css';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { Auth } from './pages/Auth';
import Dashboard from './pages/Dashboard';
import { Toaster } from 'sonner';

// Demo mode wrapper - passes isDemoMode prop to dashboard
const DemoModeWrapper = () => <Dashboard isDemoMode={true} />;

function App() {
  return (
    <AuthProvider>
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
            {/* Auth page - public */}
            <Route path="/auth" element={<Auth />} />
            
            {/* Protected routes - require login */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route 
              path="/dashboard" 
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              } 
            />
            
            {/* Legacy routes - redirect to main dashboard */}
            <Route path="/v3" element={<Navigate to="/dashboard" replace />} />
            <Route path="/v3-legacy" element={<Navigate to="/dashboard" replace />} />
            <Route path="/v4/demo" element={<Navigate to="/demo" replace />} />
            <Route path="/full-board" element={<Navigate to="/dashboard" replace />} />
            
            {/* Demo page - public for testing */}
            <Route path="/demo" element={<DemoModeWrapper />} />
            <Route path="/v3/demo" element={<DemoModeWrapper />} />
          </Routes>
        </BrowserRouter>
      </div>
    </AuthProvider>
  );
}

export default App;
