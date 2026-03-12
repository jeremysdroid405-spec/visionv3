import React from 'react';
import '@/App.css';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { Auth } from './pages/Auth';
import { Dashboard } from './pages/Dashboard';
import { DashboardDemo } from './pages/DashboardDemo';
import { FullBoard } from './pages/FullBoard';
import { Toaster } from 'sonner';

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
            <Route path="/" element={<Navigate to="/full-board" replace />} />
            <Route path="/full-board" element={<FullBoard />} />
            <Route path="/demo" element={<DashboardDemo />} />
            <Route path="/auth" element={<Auth />} />
            <Route path="/dashboard" element={<Dashboard />} />
          </Routes>
        </BrowserRouter>
      </div>
    </AuthProvider>
  );
}

export default App;