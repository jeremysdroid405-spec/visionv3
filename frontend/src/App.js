import React from 'react';
import '@/App.css';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { Auth } from './pages/Auth';
import { Dashboard } from './pages/Dashboard';
import { DashboardDemo } from './pages/DashboardDemo';
import { FullBoard } from './pages/FullBoard';
import DemonGoblinDashboard from './pages/DemonGoblinDashboard';
import DemonGoblinDashboardOptimized from './pages/DemonGoblinDashboardOptimized';
import { Toaster } from 'sonner';

// Demo mode wrapper - passes isDemoMode prop to dashboard
const DemoModeWrapper = () => <DemonGoblinDashboardOptimized isDemoMode={true} />;

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
            <Route path="/" element={<Navigate to="/v3" replace />} />
            <Route 
              path="/v3" 
              element={
                <ProtectedRoute>
                  <DemonGoblinDashboardOptimized />
                </ProtectedRoute>
              } 
            />
            <Route 
              path="/v3-legacy" 
              element={
                <ProtectedRoute>
                  <DemonGoblinDashboard />
                </ProtectedRoute>
              } 
            />
            <Route 
              path="/full-board" 
              element={
                <ProtectedRoute>
                  <FullBoard />
                </ProtectedRoute>
              } 
            />
            <Route 
              path="/dashboard" 
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              } 
            />
            
            {/* Demo page - public for testing */}
            <Route path="/demo" element={<DashboardDemo />} />
            
            {/* V3 Demo mode - public, full dashboard without auth */}
            <Route path="/v3/demo" element={<DemoModeWrapper />} />
          </Routes>
        </BrowserRouter>
      </div>
    </AuthProvider>
  );
}

export default App;