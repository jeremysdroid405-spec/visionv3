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

  useEffect(() => {
    const storedToken = localStorage.getItem('access_token');
    const storedUser = localStorage.getItem('user');
    const storedProfile = localStorage.getItem('profile');

    if (storedToken && storedUser && storedProfile) {
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
      
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify({ id: user_id, email }));
      localStorage.setItem('profile', JSON.stringify(userProfile));

      setToken(access_token);
      setUser({ id: user_id, email });
      setProfile(userProfile);

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

      setToken(access_token);
      setUser({ id: user_id, email });
      setProfile(userProfile);

      return { success: true };
    } catch (error) {
      return { 
        success: false, 
        error: error.response?.data?.detail || 'Login failed' 
      };
    }
  };

  const logout = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('profile');
    setToken(null);
    setUser(null);
    setProfile(null);
  };

  const value = {
    user,
    profile,
    token,
    loading,
    signup,
    login,
    logout,
    isAuthenticated: !!user,
    isPro: profile?.tier === 'pro',
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthContext;