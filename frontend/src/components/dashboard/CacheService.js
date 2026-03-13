// Cache Service for optimized data loading
export const CacheService = {
  get: (key) => {
    try {
      const item = localStorage.getItem(key);
      if (!item) return null;
      const { data, expiry } = JSON.parse(item);
      if (Date.now() > expiry) {
        localStorage.removeItem(key);
        return null;
      }
      return data;
    } catch {
      return null;
    }
  },
  
  set: (key, data, ttlMinutes = 5) => {
    try {
      const item = {
        data,
        expiry: Date.now() + (ttlMinutes * 60 * 1000)
      };
      localStorage.setItem(key, JSON.stringify(item));
    } catch (e) {
      console.warn('Cache write failed:', e);
    }
  },
  
  clear: (key) => {
    localStorage.removeItem(key);
  }
};
