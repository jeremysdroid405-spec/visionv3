import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

const AuthContext = createContext(null);

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState(localStorage.getItem('access_token'));
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    const storedToken = localStorage.getItem('access_token');
    const storedUser = localStorage.getItem('user');
    const storedProfile = localStorage.getItem('profile');
    const storedIsDemo = localStorage.getItem('is_demo') === 'true';

    if (storedIsDemo) {
      setIsDemo(true);
      setUser({ id: 'demo', email: 'demo@propvision.ai' });
      setProfile({ tier: 'demo', is_master: false });
    } else if (storedToken && storedUser && storedProfile) {
      setToken(storedToken);
      setUser(JSON.parse(storedUser));
      setProfile(JSON.parse(storedProfile));
    }
    setLoading(false);
  }, []);

  const signup = async (email, password, fullName) => {
    try {
      const response = await axios.post(`${API}/auth/signup`, {
        email,
        password,
        full_name: fullName,
      });

      const { access_token, user_id, profile: userProfile } = response.data;
      
      // If no access token, email confirmation required
      if (!access_token) {
        return { 
          success: true, 
          message: 'Check your email to confirm your account',
          requiresConfirmation: true
        };
      }
      
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify({ id: user_id, email }));
      localStorage.setItem('profile', JSON.stringify(userProfile));
      localStorage.removeItem('is_demo');

      setToken(access_token);
      setUser({ id: user_id, email });
      setProfile(userProfile);
      setIsDemo(false);

      return { success: true };
    } catch (error) {
      return { 
        success: false, 
        error: error.response?.data?.detail || 'Signup failed' 
      };
    }
  };

  const login = async (email, password) => {
    try {
      const response = await axios.post(`${API}/auth/login`, {
        email,
        password,
      });

      const { access_token, user_id, profile: userProfile } = response.data;
      
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify({ id: user_id, email }));
      localStorage.setItem('profile', JSON.stringify(userProfile));
      localStorage.removeItem('is_demo');

      setToken(access_token);
      setUser({ id: user_id, email });
      setProfile(userProfile);
      setIsDemo(false);

      return { success: true };
    } catch (error) {
      return { 
        success: false, 
        error: error.response?.data?.detail || 'Login failed' 
      };
    }
  };

  const enterDemoMode = () => {
    localStorage.setItem('is_demo', 'true');
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('profile');
    
    setIsDemo(true);
    setUser({ id: 'demo', email: 'demo@propvision.ai' });
    setProfile({ tier: 'demo', is_master: false });
    setToken(null);
  };

  const logout = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('profile');
    localStorage.removeItem('is_demo');
    setToken(null);
    setUser(null);
    setProfile(null);
    setIsDemo(false);
  };

  const value = {
    user,
    profile,
    token,
    loading,
    isDemo,
    signup,
    login,
    logout,
    enterDemoMode,
    isAuthenticated: !!user || isDemo,
    isPro: profile?.tier === 'pro' || profile?.tier === 'master',
    isMaster: profile?.is_master === true || profile?.tier === 'master',
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthContext;