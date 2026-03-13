import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { 
  Eye, Zap, Target, Shield, ChevronDown, 
  Play, Lock, Crosshair, Radio, Cpu
} from 'lucide-react';
import { toast } from 'sonner';

// ==================== ELITE ICON COMPONENTS ====================

const DemonIcon = ({ size = 24, className = '' }) => (
  <svg 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none" 
    xmlns="http://www.w3.org/2000/svg"
    className={`demon-glow ${className}`}
  >
    <defs>
      <filter id="demon-glow-auth" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="1.5" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
    </defs>
    <path d="M12 22C16.4183 22 20 18.4183 20 14C20 9.58172 16.4183 6 12 6C7.58172 6 4 9.58172 4 14C4 18.4183 7.58172 22 12 22Z" fill="#FF0000" filter="url(#demon-glow-auth)"/>
    <path d="M5 8L2 2L9 5" stroke="#FF0000" strokeWidth="2.5" strokeLinejoin="round"/>
    <path d="M19 8L22 2L15 5" stroke="#FF0000" strokeWidth="2.5" strokeLinejoin="round"/>
    <path d="M8 12L10 14M16 12L14 14" stroke="white" strokeWidth="2" strokeLinecap="round"/>
  </svg>
);

const GoblinIcon = ({ size = 24, className = '' }) => (
  <svg 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none" 
    xmlns="http://www.w3.org/2000/svg"
    className={`goblin-glow ${className}`}
  >
    <path d="M12 20C15.866 20 19 16.866 19 13C19 9.13401 15.866 6 12 6C8.13401 6 5 9.13401 5 13C5 16.866 8.13401 20 12 20Z" fill="#00FF7F" fillOpacity="0.9"/>
    <path d="M5 11L1 7L6 12" fill="#00FF7F"/>
    <path d="M19 11L23 7L18 12" fill="#00FF7F"/>
    <path d="M9 13H10M14 13H15" stroke="black" strokeWidth="2.5" strokeLinecap="round"/>
    <path d="M10 16.5C10.5 17.5 13.5 17.5 14 16.5" stroke="black" strokeWidth="1" strokeLinecap="round"/>
  </svg>
);

// ==================== ANIMATED COMPONENTS ====================

const ScanningBar = ({ isActive, speed = 3 }) => (
  <div 
    className="absolute left-0 right-0 h-0.5 bg-gradient-to-r from-transparent via-emerald-400 to-transparent shadow-lg shadow-emerald-500/50"
    style={{
      animation: isActive ? `scan-down ${speed}s ease-in-out infinite` : 'none',
      top: '0%'
    }}
  />
);

const DataStream = () => (
  <div className="absolute inset-0 overflow-hidden opacity-15 pointer-events-none">
    {[...Array(25)].map((_, i) => (
      <div
        key={i}
        className="absolute text-emerald-500/40 font-mono text-[10px] whitespace-nowrap"
        style={{
          left: `${Math.random() * 100}%`,
          top: `${Math.random() * 100}%`,
          animation: `float-up ${5 + Math.random() * 10}s linear infinite`,
          animationDelay: `${Math.random() * 5}s`
        }}
      >
        {['01001110', '10110101', 'SCAN_OK', 'TARGET_ACQ', '0xFF00'][Math.floor(Math.random() * 5)]}
      </div>
    ))}
  </div>
);

// System Status Terminal Line
const TerminalLine = ({ label, status, statusColor = 'emerald', delay = 0 }) => {
  const [visible, setVisible] = useState(false);
  
  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(timer);
  }, [delay]);
  
  if (!visible) return <div className="h-5" />;
  
  return (
    <div className="flex items-center justify-between font-mono text-xs animate-fade-in">
      <span className="text-zinc-500">[{label}]</span>
      <span className={`text-${statusColor}-400 font-bold`}>{status}</span>
    </div>
  );
};

// Kill List Spec Row - Enhanced Industrial Design
const SpecRow = ({ spec, title, edge, icon }) => (
  <div className="py-5 border-b border-zinc-800/30 last:border-0 group">
    <div className="flex items-start gap-4">
      <div className="flex-shrink-0 w-12 h-12 rounded-lg border border-zinc-700/50 bg-zinc-900/80 flex items-center justify-center group-hover:border-emerald-800/50 group-hover:bg-zinc-900 transition-all">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-emerald-500 font-mono text-xs uppercase tracking-widest font-bold">{spec}</span>
          <div className="flex-1 h-px bg-gradient-to-r from-zinc-800 to-transparent" />
        </div>
        <p className="text-white font-medium text-sm mb-2">{title}</p>
        <p className="text-zinc-500 text-xs leading-relaxed">
          <span className="text-zinc-400 font-semibold">The Edge:</span> {edge}
        </p>
      </div>
    </div>
  </div>
);

// ==================== MAIN AUTH COMPONENT ====================

export const Auth = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { signup, login, isAuthenticated } = useAuth();
  const [loading, setLoading] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [scanSpeed, setScanSpeed] = useState(3);
  const formRef = useRef(null);
  
  // Simulated live stats
  const [demonCount, setDemonCount] = useState(14);
  const [goblinCount, setGoblinCount] = useState(8);
  
  // Form states
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    fullName: '',
  });
  const [isLogin, setIsLogin] = useState(true);

  const from = location.state?.from?.pathname || '/v3';

  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from]);

  // Simulate live target updates
  useEffect(() => {
    const interval = setInterval(() => {
      setDemonCount(prev => prev + Math.floor(Math.random() * 3) - 1);
      setGoblinCount(prev => prev + Math.floor(Math.random() * 2));
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Speed up scan when typing
  useEffect(() => {
    if (isTyping) {
      setScanSpeed(0.8);
    } else {
      setScanSpeed(3);
    }
  }, [isTyping]);

  const handleInputChange = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    setIsTyping(value.length > 0);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    if (isLogin) {
      const result = await login(formData.email, formData.password);
      setLoading(false);
      if (result.success) {
        document.body.classList.add('flash-silver');
        setTimeout(() => {
          document.body.classList.remove('flash-silver');
          toast.success('SYSTEM ACTIVATED');
          navigate(from, { replace: true });
        }, 300);
      } else {
        toast.error(result.error);
      }
    } else {
      const result = await signup(formData.email, formData.password, formData.fullName);
      setLoading(false);
      if (result.success) {
        toast.success('Account created! Check your email to confirm.');
        setIsLogin(true);
      } else {
        toast.error(result.error);
      }
    }
  };

  const scrollToForm = () => {
    setTimeout(() => {
      formRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  };

  return (
    <div className="min-h-screen bg-black overflow-x-hidden">
      {/* CSS Animations */}
      <style>{`
        @keyframes scan-down {
          0%, 100% { top: 0%; opacity: 0; }
          10% { opacity: 1; }
          90% { opacity: 1; }
          100% { top: 100%; opacity: 0; }
        }
        @keyframes float-up {
          0% { transform: translateY(100vh); opacity: 0; }
          10% { opacity: 0.3; }
          90% { opacity: 0.3; }
          100% { transform: translateY(-100vh); opacity: 0; }
        }
        @keyframes glow-pulse {
          0%, 100% { filter: drop-shadow(0 0 8px currentColor); }
          50% { filter: drop-shadow(0 0 20px currentColor); }
        }
        @keyframes fade-in {
          0% { opacity: 0; transform: translateX(-10px); }
          100% { opacity: 1; transform: translateX(0); }
        }
        @keyframes typewriter {
          from { width: 0; }
          to { width: 100%; }
        }
        @keyframes blink {
          0%, 50% { opacity: 1; }
          51%, 100% { opacity: 0; }
        }
        .demon-glow { animation: glow-pulse 2s ease-in-out infinite; color: #FF0000; }
        .goblin-glow { animation: glow-pulse 2s ease-in-out infinite; color: #00FF7F; }
        .animate-fade-in { animation: fade-in 0.5s ease-out forwards; }
        .flash-silver { animation: flash 0.3s ease-out; }
        @keyframes flash {
          0% { filter: brightness(1); }
          50% { filter: brightness(2.5) saturate(0); }
          100% { filter: brightness(1); }
        }
        .gradient-text-silver {
          background: linear-gradient(90deg, #71717a, #fafafa, #71717a);
          background-size: 200% auto;
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          animation: gradient-shift 4s ease infinite;
        }
        @keyframes gradient-shift {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }
        .terminal-cursor::after {
          content: '_';
          animation: blink 1s step-end infinite;
        }
      `}</style>

      {/* ==================== SECTION 1: HERO ==================== */}
      <section className="min-h-screen flex flex-col items-center justify-center relative px-4 py-12">
        <DataStream />
        
        {/* Logo */}
        <div className="relative z-10 flex flex-col items-center">
          <div className="flex items-center gap-3 mb-3">
            <Crosshair className="w-6 h-6 text-zinc-600" />
            <Eye className="w-8 h-8 text-zinc-400" />
            <Crosshair className="w-6 h-6 text-zinc-600" />
          </div>
          <h1 className="text-5xl md:text-7xl font-black tracking-tighter gradient-text-silver">
            PICKVISION
          </h1>
          <span className="text-sm text-zinc-600 font-mono tracking-[0.3em] mt-1">v3.0 // FLASH ARCHITECTURE</span>
        </div>

        {/* THE Headline - War Room Version */}
        <div className="mt-8 text-center relative z-10 max-w-2xl">
          <p className="text-xl md:text-2xl text-zinc-500 font-light tracking-wide">
            The books have an edge.
          </p>
          <p className="text-2xl md:text-3xl text-white font-bold mt-2">
            Now, you have a <span className="text-emerald-400">weapon</span>.
          </p>
        </div>

        {/* Live Scan Visual */}
        <div className="mt-10 w-full max-w-lg relative z-10">
          <div className="relative bg-zinc-950 rounded-xl border border-zinc-800/50 overflow-hidden">
            <ScanningBar isActive={true} speed={scanSpeed} />
            
            {/* Terminal Header */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-zinc-800/50 bg-zinc-900/50">
              <div className="w-2 h-2 rounded-full bg-red-500" />
              <div className="w-2 h-2 rounded-full bg-yellow-500" />
              <div className="w-2 h-2 rounded-full bg-green-500" />
              <span className="ml-2 text-zinc-600 text-xs font-mono">PICKVISION_TERMINAL</span>
            </div>
            
            {/* Terminal Content */}
            <div className="p-4 space-y-2">
              <div className="flex items-center gap-2 text-xs font-mono">
                <Radio className="w-3 h-3 text-emerald-500 animate-pulse" />
                <span className="text-zinc-600">[SCANNING TANK01 FEEDS...]</span>
                <span className="text-emerald-400">STABLE</span>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <Lock className="w-3 h-3 text-emerald-500" />
                <span className="text-zinc-600">[LLM HANDSHAKE...]</span>
                <span className="text-emerald-400">ENCRYPTED</span>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <DemonIcon size={14} />
                <span className="text-zinc-600">[DEMON TARGETS DETECTED...]</span>
                <span className="text-red-400 font-bold">{demonCount}</span>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <GoblinIcon size={14} />
                <span className="text-zinc-600">[GOBLIN LOCKS FOUND...]</span>
                <span className="text-emerald-400 font-bold">{goblinCount}</span>
              </div>
            </div>
            
            {/* Blinking Cursor Line */}
            <div className="px-4 pb-4">
              <span className="text-emerald-500 font-mono text-xs terminal-cursor">
                &gt; AWAITING OPERATOR AUTHENTICATION
              </span>
            </div>
          </div>
        </div>

        {/* CTA Button */}
        <Button
          onClick={scrollToForm}
          data-testid="enter-vault-btn"
          className="mt-8 px-10 py-6 text-base font-mono font-bold bg-emerald-500 hover:bg-emerald-400 text-black rounded border border-emerald-400 shadow-lg shadow-emerald-500/30 hover:shadow-emerald-400/50 transition-all duration-300 hover:scale-105 relative z-10 tracking-wider"
        >
          [ CLAIM YOUR EDGE ]
        </Button>

        {/* Scroll Indicator */}
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 text-zinc-700 animate-bounce">
          <ChevronDown className="w-6 h-6" />
        </div>
      </section>

      {/* ==================== SECTION 2: THE KILL LIST ==================== */}
      <section className="py-20 px-4 bg-zinc-950 border-y border-zinc-900">
        <div className="max-w-3xl mx-auto">
          {/* Section Header */}
          <div className="text-center mb-10">
            <span className="text-zinc-600 font-mono text-[10px] tracking-[0.3em]">TECHNICAL SPECIFICATIONS</span>
            <h2 className="text-2xl md:text-3xl font-bold text-white mt-2 tracking-tight">
              DEPLOYMENT: <span className="text-emerald-400">THE KILL LIST</span>
            </h2>
            <div className="flex items-center justify-center gap-2 mt-3">
              <span className="text-zinc-600 font-mono text-xs">SYSTEM STATUS:</span>
              <span className="text-emerald-400 font-mono text-xs font-bold animate-pulse">[OPERATIONAL]</span>
            </div>
          </div>
          
          {/* Spec Table */}
          <div className="bg-gradient-to-b from-zinc-900/50 to-zinc-950 rounded-2xl border border-zinc-800/50 p-6 md:p-8">
            <SpecRow 
              spec="MODEL"
              title="PickVision v3.0 (Flash Architecture)"
              edge="Built on ultra-low latency infrastructure. While other apps are still refreshing, PickVision has already processed the play. No lag, no delays—just raw speed."
              icon={<Cpu className="w-6 h-6 text-zinc-400" />}
            />
            <SpecRow 
              spec="LOGIC"
              title="Anomaly Detection"
              edge="We ignore the 'standard.' Most models chase averages; we hunt the glitches. Our AI identifies the 1% of lines where the sportsbooks' math has failed."
              icon={<Zap className="w-6 h-6 text-yellow-500" />}
            />
            <SpecRow 
              spec="INTEL"
              title="Usage Ripple™"
              edge="Automated battlefield recalculation. When injury news drops, the Ripple evaluates the floor and ceiling for the entire roster within 60s. You get the new line before the spread moves."
              icon={<Radio className="w-6 h-6 text-cyan-400" />}
            />
            <SpecRow 
              spec="TARGETING"
              title="Demon Radar"
              edge="High-alpha, high-variance line exploits. This isn't for the faint of heart. The Radar targets the high-multiplier kills that turn a session into a season."
              icon={<DemonIcon size={24} />}
            />
            <SpecRow 
              spec="SAFETY"
              title="Goblin Recon"
              edge="Surgical bankroll stabilization. Recon isn't about luck; it's about the math of the 'Lock.' High-probability infiltration data for consistent resource accumulation."
              icon={<GoblinIcon size={24} />}
            />
          </div>
        </div>
      </section>

      {/* ==================== SECTION 3: SIGNUP FORM ==================== */}
      <section ref={formRef} className="py-20 px-4 bg-black" id="signup-section">
        <div className="max-w-md mx-auto">
          {/* War Room Quote */}
          <div className="text-center mb-8">
            <p className="text-zinc-600 font-mono text-xs tracking-widest mb-4">// OPERATOR AUTHENTICATION</p>
            <h2 className="text-xl md:text-2xl font-bold gradient-text-silver tracking-wide uppercase">
              "The books have an edge.<br/>Now, you have a weapon."
            </h2>
          </div>

          {/* System Status Terminal */}
          <div className="bg-zinc-950 rounded-lg border border-zinc-800/50 p-4 mb-6 font-mono text-xs">
            <TerminalLine label="SCANNING TANK01 FEEDS..." status="STABLE" delay={0} />
            <TerminalLine label="LLM HANDSHAKE..." status="ENCRYPTED" delay={300} />
            <TerminalLine label="DEMON TARGETS DETECTED..." status={demonCount.toString()} statusColor="red" delay={600} />
            <TerminalLine label="GOBLIN LOCKS FOUND..." status={goblinCount.toString()} delay={900} />
          </div>

          {/* Auth Form */}
          <div className="bg-zinc-950 rounded-xl border border-zinc-800/50 p-6 relative overflow-hidden">
            {isTyping && <ScanningBar isActive={true} speed={0.8} />}
            
            {/* Social Auth Buttons */}
            <div className="space-y-3 mb-6">
              <Button
                type="button"
                variant="outline"
                className="w-full bg-white hover:bg-zinc-100 text-black border-none py-5 font-medium"
                onClick={() => toast.info('Google Auth coming soon!')}
              >
                <svg className="w-5 h-5 mr-2" viewBox="0 0 24 24">
                  <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                  <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Continue with Google
              </Button>
              
              <Button
                type="button"
                variant="outline"
                className="w-full bg-zinc-900 hover:bg-zinc-800 text-white border-zinc-700 py-5 font-medium"
                onClick={() => toast.info('Apple Auth coming soon!')}
              >
                <svg className="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                </svg>
                Continue with Apple
              </Button>
            </div>

            {/* Divider */}
            <div className="flex items-center gap-4 my-6">
              <div className="flex-1 h-px bg-zinc-800" />
              <span className="text-zinc-700 text-xs font-mono">OR</span>
              <div className="flex-1 h-px bg-zinc-800" />
            </div>

            {/* Traditional Form */}
            <form onSubmit={handleSubmit} className="space-y-4">
              {!isLogin && (
                <div className="space-y-2">
                  <Label htmlFor="fullName" className="text-zinc-500 text-xs font-mono uppercase tracking-wider">
                    Operator Name
                  </Label>
                  <Input
                    id="fullName"
                    data-testid="signup-name"
                    type="text"
                    placeholder="Enter callsign"
                    value={formData.fullName}
                    onChange={(e) => handleInputChange('fullName', e.target.value)}
                    className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-5 font-mono"
                  />
                </div>
              )}
              
              <div className="space-y-2">
                <Label htmlFor="email" className="text-zinc-500 text-xs font-mono uppercase tracking-wider">
                  Email
                </Label>
                <Input
                  id="email"
                  data-testid={isLogin ? "login-email" : "signup-email"}
                  type="email"
                  placeholder="operator@domain.com"
                  value={formData.email}
                  onChange={(e) => handleInputChange('email', e.target.value)}
                  required
                  className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-5 font-mono"
                />
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="password" className="text-zinc-500 text-xs font-mono uppercase tracking-wider">
                  Access Key
                </Label>
                <Input
                  id="password"
                  data-testid={isLogin ? "login-password" : "signup-password"}
                  type="password"
                  placeholder="••••••••••••"
                  value={formData.password}
                  onChange={(e) => handleInputChange('password', e.target.value)}
                  required
                  minLength={6}
                  className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-5 font-mono"
                />
              </div>

              <Button
                type="submit"
                data-testid={isLogin ? "login-submit-btn" : "signup-submit-btn"}
                disabled={loading}
                className="w-full py-6 text-sm font-mono font-bold bg-emerald-500 hover:bg-emerald-400 text-black rounded border border-emerald-400 shadow-lg shadow-emerald-500/20 hover:shadow-emerald-400/40 transition-all duration-300 tracking-widest uppercase"
              >
                {loading ? (
                  <div className="flex items-center gap-2 justify-center">
                    <div className="w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                    AUTHENTICATING...
                  </div>
                ) : (
                  <>[ CLAIM YOUR EDGE ]</>
                )}
              </Button>
            </form>

            {/* Social Proof */}
            <p className="text-center text-zinc-700 text-xs mt-4 font-mono">
              <span className="text-emerald-500">12,402</span> operators active
            </p>

            {/* Toggle Login/Signup */}
            <div className="mt-6 pt-6 border-t border-zinc-800/50 text-center">
              <button
                type="button"
                onClick={() => setIsLogin(!isLogin)}
                className="text-zinc-600 text-xs font-mono hover:text-white transition-colors"
              >
                {isLogin ? "NEW OPERATOR? " : "EXISTING OPERATOR? "}
                <span className="text-emerald-400 font-bold">
                  {isLogin ? '[REGISTER]' : '[LOGIN]'}
                </span>
              </button>
            </div>
          </div>

          {/* Demo Mode */}
          <div className="mt-8 text-center">
            <Button
              onClick={() => navigate('/v3/demo')}
              data-testid="try-demo-btn"
              variant="ghost"
              className="text-zinc-600 hover:text-white hover:bg-zinc-900/50 transition-all font-mono text-xs"
            >
              <Play className="w-4 h-4 mr-2" />
              [ DEMO MODE ]
            </Button>
            <p className="text-zinc-800 text-[10px] mt-1 font-mono">
              Explore without authentication
            </p>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-6 px-4 border-t border-zinc-900">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Eye className="w-4 h-4 text-zinc-700" />
            <span className="text-zinc-700 text-xs font-mono">PICKVISION AI</span>
          </div>
          <span className="text-zinc-800 text-xs font-mono">© 2026</span>
        </div>
      </footer>
    </div>
  );
};

export default Auth;
