import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Eye, Zap, Target, Shield, ChevronDown,
  Play, Lock, Crosshair, Radio, Cpu, Newspaper,
  TrendingUp, AlertTriangle, Layers, BarChart3, ShieldCheck
} from 'lucide-react';
import { toast } from 'sonner';

// ==================== CUSTOM TACTICAL ICONS ====================
const BrainCircuitIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M12 2a6 6 0 0 1 6 6c0 2-1 3.5-2 4.5M12 2a6 6 0 0 0-6 6c0 2 1 3.5 2 4.5" />
    <circle cx="12" cy="16" r="4" />
    <path d="M12 12v-2M8 16H4M20 16h-4M12 20v2" />
    <circle cx="12" cy="16" r="1" fill="currentColor" />
  </svg>
);

const RadarSweepIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="10" strokeDasharray="3 3" />
    <circle cx="12" cy="12" r="6" />
    <circle cx="12" cy="12" r="2" fill="currentColor" />
    <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    <path d="M12 12L18 6" strokeWidth="2" />
  </svg>
);

const WaveformIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M2 12h2l2-8 3 16 3-12 2 8 2-4h6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const NetworkPulseIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="12" cy="12" r="3" fill="currentColor" />
    <circle cx="4" cy="8" r="2" />
    <circle cx="20" cy="8" r="2" />
    <circle cx="4" cy="16" r="2" />
    <circle cx="20" cy="16" r="2" />
    <path d="M6 8l3 2.5M18 8l-3 2.5M6 16l3-2.5M18 16l-3-2.5" />
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
  
  // Map color names to explicit Tailwind classes for proper compilation
  const colorClasses = {
    emerald: 'text-emerald-400',
    red: 'text-red-400',
    blue: 'text-blue-400',
    orange: 'text-orange-400',
    green: 'text-green-400',
    yellow: 'text-yellow-400',
  };
  
  return (
    <div className="flex items-center justify-between font-mono text-xs animate-fade-in">
      <span className="text-zinc-500">[{label}]</span>
      <span className={`${colorClasses[statusColor] || colorClasses.emerald} font-bold`}>{status}</span>
    </div>
  );
};

// Kill List Spec Row - Enhanced Industrial Design (Mobile Optimized)
const SpecRow = ({ spec, title, edge, icon }) => (
  <div className="py-4 sm:py-5 border-b border-zinc-800/30 last:border-0 group">
    <div className="flex items-start gap-3 sm:gap-4">
      <div className="flex-shrink-0 w-10 h-10 sm:w-12 sm:h-12 rounded-lg border border-zinc-700/50 bg-zinc-900/80 flex items-center justify-center group-hover:border-emerald-800/50 group-hover:bg-zinc-900 transition-all">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 sm:gap-3 mb-1">
          <span className="text-emerald-500 font-mono text-[10px] sm:text-xs uppercase tracking-widest font-bold">{spec}</span>
          <div className="flex-1 h-px bg-gradient-to-r from-zinc-800 to-transparent" />
        </div>
        <p className="text-white font-medium text-sm sm:text-sm mb-1.5 sm:mb-2">{title}</p>
        <div className="text-zinc-400 text-xs sm:text-xs leading-relaxed">
          <span className="text-zinc-300 font-semibold">The Edge:</span> {edge}
        </div>
      </div>
    </div>
  </div>
);

// ==================== MAIN AUTH COMPONENT ====================

export const Auth = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { signup, login, isAuthenticated, enterDemoMode } = useAuth();
  const [loading, setLoading] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [scanSpeed, setScanSpeed] = useState(3);
  const formRef = useRef(null);
  
  // Countdown state
  const [showCountdown, setShowCountdown] = useState(false);
  const [countdown, setCountdown] = useState(5);
  const [countdownMessage, setCountdownMessage] = useState('');
  
  // Simulated live stats
  const [sharpEdges, setSharpEdges] = useState(14);
  const [trapAlerts, setTrapAlerts] = useState(8);
  
  // Form states
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    fullName: '',
  });
  const [isLogin, setIsLogin] = useState(true);

  const from = location.state?.from?.pathname || '/v3';

  useEffect(() => {
    if (isAuthenticated && !showCountdown) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from, showCountdown]);
  
  // Countdown effect
  useEffect(() => {
    if (showCountdown && countdown > 0) {
      const timer = setTimeout(() => setCountdown(prev => prev - 1), 1000);
      return () => clearTimeout(timer);
    } else if (showCountdown && countdown === 0) {
      navigate('/v3', { replace: true });
    }
  }, [showCountdown, countdown, navigate]);

  // Simulate live target updates
  useEffect(() => {
    const interval = setInterval(() => {
      setSharpEdges(prev => prev + Math.floor(Math.random() * 3) - 1);
      setTrapAlerts(prev => prev + Math.floor(Math.random() * 2));
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
  
  // Start countdown animation
  const startCountdown = (message) => {
    setCountdownMessage(message);
    setCountdown(3);
    setShowCountdown(true);
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
          startCountdown('OPERATOR AUTHENTICATED');
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
  
  const handleDemoMode = () => {
    enterDemoMode();
    startCountdown('SYSTEM ACTIVATED');
  };

  const scrollToForm = () => {
    setTimeout(() => {
      formRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  };

  return (
    <div className="min-h-screen bg-black overflow-x-hidden">
      {/* Countdown Overlay */}
      {showCountdown && (
        <div className="fixed inset-0 z-[100] bg-black flex items-center justify-center">
          <div className="text-center">
            {/* Scanning rings */}
            <div className="relative w-48 h-48 sm:w-64 sm:h-64 mx-auto mb-8">
              {/* Outer ring */}
              <div className="absolute inset-0 rounded-full border-2 border-emerald-500/30 animate-ping" style={{ animationDuration: '2s' }} />
              {/* Middle ring */}
              <div className="absolute inset-4 rounded-full border border-emerald-500/50 animate-pulse" />
              {/* Inner ring with countdown */}
              <div className="absolute inset-8 rounded-full border-2 border-emerald-500 flex items-center justify-center bg-zinc-950/80">
                <span className="text-7xl sm:text-8xl font-black text-emerald-400 font-mono animate-pulse">
                  {countdown}
                </span>
              </div>
              {/* Rotating scanner line */}
              <div 
                className="absolute inset-0 rounded-full"
                style={{
                  background: 'conic-gradient(from 0deg, transparent 0deg, rgba(16, 185, 129, 0.3) 30deg, transparent 60deg)',
                  animation: 'spin 1.5s linear infinite'
                }}
              />
            </div>
            
            {/* Status message */}
            <div className="space-y-3">
              <p className="text-emerald-400 font-mono text-sm sm:text-base tracking-[0.3em] animate-pulse">
                {countdownMessage}
              </p>
              <div className="flex items-center justify-center gap-2 text-zinc-500 font-mono text-xs">
                <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span>INITIALIZING PROPVISION AI</span>
              </div>
              <div className="flex items-center justify-center gap-4 text-zinc-600 font-mono text-[10px] mt-4">
                <span className="flex items-center gap-1">
                  <Cpu className="w-3 h-3" />
                  LOADING INTEL
                </span>
                <span className="flex items-center gap-1">
                  <Radio className="w-3 h-3 animate-pulse text-emerald-500" />
                  SYNCING FEEDS
                </span>
                <span className="flex items-center gap-1">
                  <Target className="w-3 h-3" />
                  SCANNING TARGETS
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
      
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
        .sharp-glow { animation: glow-pulse 2s ease-in-out infinite; color: #3B82F6; }
        .trap-glow { animation: glow-pulse 2s ease-in-out infinite; color: #F97316; }
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
      <section className="min-h-[85vh] sm:min-h-screen flex flex-col items-center justify-center relative px-4 py-8 sm:py-12">
        <DataStream />
        
        {/* Logo */}
        <div className="relative z-10 flex flex-col items-center">
          <h1 className="text-4xl sm:text-5xl md:text-7xl font-black tracking-tighter gradient-text-silver">
            PROPVISION AI
          </h1>
          <span className="text-[10px] sm:text-sm text-zinc-600 font-mono tracking-[0.2em] sm:tracking-[0.3em] mt-1">v3.0 // FLASH ARCHITECTURE</span>
        </div>

        {/* THE Headline - War Room Version */}
        <div className="mt-5 sm:mt-8 text-center relative z-10 max-w-2xl">
          <p className="text-lg sm:text-xl md:text-2xl text-zinc-500 font-light tracking-wide">
            The books have the edge.
          </p>
          <p className="text-xl sm:text-2xl md:text-3xl text-white font-bold mt-1 sm:mt-2">
            Now, you have the <span className="text-emerald-400">weapon</span>.
          </p>
        </div>

        {/* Live Scan Visual */}
        <div className="mt-6 sm:mt-10 w-full max-w-lg relative z-10">
          <div className="relative bg-zinc-950 rounded-xl border border-zinc-800/50 overflow-hidden">
            <ScanningBar isActive={true} speed={scanSpeed} />
            
            {/* Terminal Header */}
            <div className="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-zinc-800/50 bg-zinc-900/50">
              <div className="w-2 h-2 rounded-full bg-red-500" />
              <div className="w-2 h-2 rounded-full bg-yellow-500" />
              <div className="w-2 h-2 rounded-full bg-green-500" />
              <span className="ml-2 text-zinc-600 text-[10px] sm:text-xs font-mono">PROPVISION_AI_TERMINAL</span>
            </div>
            
            {/* Terminal Content */}
            <div className="p-3 sm:p-4 space-y-1.5 sm:space-y-2">
              <div className="flex items-center gap-2 text-[10px] sm:text-xs font-mono">
                <Radio className="w-3 h-3 text-emerald-500 animate-pulse" />
                <span className="text-zinc-600">[SCANNING BALL IS LIFE FEEDS...]</span>
                <span className="text-emerald-400">STABLE</span>
              </div>
              <div className="flex items-center gap-2 text-[10px] sm:text-xs font-mono">
                <Lock className="w-3 h-3 text-emerald-500" />
                <span className="text-zinc-600">[LLM HANDSHAKE...]</span>
                <span className="text-emerald-400">ENCRYPTED</span>
              </div>
              <div className="flex items-center gap-2 text-[10px] sm:text-xs font-mono">
                <TrendingUp className="w-3 h-3 text-blue-500" />
                <span className="text-zinc-600">[SHARP EDGES DETECTED...]</span>
                <span className="text-blue-400 font-bold">{sharpEdges}</span>
              </div>
              <div className="flex items-center gap-2 text-[10px] sm:text-xs font-mono">
                <AlertTriangle className="w-3 h-3 text-orange-500" />
                <span className="text-zinc-600">[TRAP LINES FLAGGED...]</span>
                <span className="text-orange-400 font-bold">{trapAlerts}</span>
              </div>
            </div>
            
            {/* Blinking Cursor Line */}
            <div className="px-3 sm:px-4 pb-3 sm:pb-4">
              <span className="text-emerald-500 font-mono text-[10px] sm:text-xs terminal-cursor">
                &gt; AWAITING OPERATOR AUTHENTICATION
              </span>
            </div>
          </div>
        </div>

        {/* CTA Button */}
        <Button
          onClick={scrollToForm}
          data-testid="enter-vault-btn"
          className="mt-6 sm:mt-8 px-8 sm:px-10 py-5 sm:py-6 text-sm sm:text-base font-mono font-bold bg-emerald-500 hover:bg-emerald-400 text-black rounded border border-emerald-400 shadow-lg shadow-emerald-500/30 hover:shadow-emerald-400/50 transition-all duration-300 hover:scale-105 relative z-10 tracking-wider"
        >
          [ CLAIM YOUR EDGE ]
        </Button>

        {/* Scroll Indicator */}
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 text-zinc-700 animate-bounce">
          <ChevronDown className="w-6 h-6" />
        </div>
      </section>

      {/* ==================== SECTION 2: PROPVISION ==================== */}
      <section className="py-12 sm:py-20 px-4 bg-zinc-950 border-y border-zinc-900">
        <div className="max-w-3xl mx-auto">
          {/* Section Header */}
          <div className="text-center mb-6 sm:mb-10">
            <span className="text-zinc-600 font-mono text-[9px] sm:text-[10px] tracking-[0.2em] sm:tracking-[0.3em]">TECHNICAL SPECIFICATIONS</span>
            <h2 className="text-xl sm:text-2xl md:text-3xl font-bold text-white mt-2 tracking-tight">
              DEPLOYMENT: <span className="text-emerald-400">PROPVISION AI™</span>
            </h2>
            <p className="text-zinc-400 text-sm sm:text-base mt-3 sm:mt-4">
              The books have the edge. <span className="text-white font-semibold">Now you have the weapon.</span>
            </p>
            <div className="flex items-center justify-center gap-2 mt-3 sm:mt-4">
              <span className="text-zinc-600 font-mono text-[10px] sm:text-xs">SYSTEM STATUS:</span>
              <span className="text-emerald-400 font-mono text-[10px] sm:text-xs font-bold animate-pulse">[CORE_3.0 // INTEL_LAYER_ACTIVE // LIVE_FEED_SYNCED]</span>
            </div>
          </div>

          {/* Spec Table */}
          <div className="bg-gradient-to-b from-zinc-900/50 to-zinc-950 rounded-2xl border border-zinc-800/50 p-4 sm:p-6 md:p-8">
            <SpecRow
              spec="CORE"
              title="VISION CORE 3.0 — Multi-Layer Regression"
              edge="Calculates true outcome probability independent of the line. Built on 186 features across five seasons, with rolling metrics that capture form, role, and consistency, not averages."
              icon={<Cpu className="w-6 h-6 sm:w-7 sm:h-7 text-emerald-400" strokeWidth={1.5} />}
            />
            <SpecRow
              spec="INTEL"
              title="VISION INTEL — Intelligence Layer"
              edge="Every play is processed for projection, hit profile, volatility, and structural fit. Environmental signals, heat streaks, and system badges surface the context the line doesn't show."
              icon={<Layers className="w-6 h-6 sm:w-7 sm:h-7 text-emerald-400" strokeWidth={1.5} />}
            />
            <SpecRow
              spec="LIVE"
              title="VISION LIVE — Injury & Role Detection"
              edge="Detects lineup and role changes as they happen and recalculates projections immediately. Value shifts are surfaced as they form, not after the market adjusts."
              icon={<Radio className="w-6 h-6 sm:w-7 sm:h-7 text-emerald-400" strokeWidth={1.5} />}
            />
            <SpecRow
              spec="SIGNAL"
              title="VISION SIGNAL — Tiered Scoring"
              edge="Separates strength, risk, and confirmation into three independent tiers. Plays are classified on probability, volatility, and market alignment, never on a single blended score."
              icon={<BarChart3 className="w-6 h-6 sm:w-7 sm:h-7 text-emerald-400" strokeWidth={1.5} />}
            />
            <SpecRow
              spec="GUARD"
              title="VISION GUARD — Playability Filter"
              edge="Enforces structural constraints and removes non-playable configurations at the scoring layer. Only plays that meet execution criteria reach the board."
              icon={<ShieldCheck className="w-6 h-6 sm:w-7 sm:h-7 text-emerald-400" strokeWidth={1.5} />}
            />
          </div>
        </div>
      </section>

      {/* ==================== SECTION 3: SIGNUP FORM ==================== */}
      <section ref={formRef} className="py-12 sm:py-20 px-4 bg-black pb-24 sm:pb-20" id="signup-section">
        <div className="max-w-md mx-auto">
          {/* War Room Quote */}
          <div className="text-center mb-6 sm:mb-8">
            <p className="text-zinc-600 font-mono text-[10px] sm:text-xs tracking-widest mb-3 sm:mb-4">// OPERATOR AUTHENTICATION</p>
            <h2 className="text-lg sm:text-xl md:text-2xl font-bold gradient-text-silver tracking-wide uppercase">
              "The books have the edge.<br/>Now, you have the weapon."
            </h2>
          </div>

          {/* System Status Terminal */}
          <div className="bg-zinc-950 rounded-lg border border-zinc-800/50 p-3 sm:p-4 mb-4 sm:mb-6 font-mono text-[10px] sm:text-xs">
            <TerminalLine label="SCANNING BALL IS LIFE FEEDS..." status="STABLE" delay={0} />
            <TerminalLine label="LLM HANDSHAKE..." status="ENCRYPTED" delay={300} />
            <TerminalLine label="SHARP EDGES DETECTED..." status={sharpEdges.toString()} statusColor="blue" delay={600} />
            <TerminalLine label="TRAP LINES FLAGGED..." status={trapAlerts.toString()} statusColor="orange" delay={900} />
          </div>

          {/* Auth Form */}
          <div className="bg-zinc-950 rounded-xl border border-zinc-800/50 p-4 sm:p-6 relative overflow-hidden">
            {isTyping && <ScanningBar isActive={true} speed={0.8} />}
            
            {/* Social Auth Buttons */}
            <div className="space-y-2 sm:space-y-3 mb-4 sm:mb-6">
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
                className="w-full bg-zinc-900 hover:bg-zinc-800 text-white border-zinc-700 py-4 sm:py-5 font-medium text-sm"
                onClick={() => toast.info('Apple Auth coming soon!')}
              >
                <svg className="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                </svg>
                Continue with Apple
              </Button>
            </div>

            {/* Divider */}
            <div className="flex items-center gap-4 my-4 sm:my-6">
              <div className="flex-1 h-px bg-zinc-800" />
              <span className="text-zinc-700 text-[10px] sm:text-xs font-mono">OR</span>
              <div className="flex-1 h-px bg-zinc-800" />
            </div>

            {/* Traditional Form */}
            <form onSubmit={handleSubmit} className="space-y-3 sm:space-y-4">
              {!isLogin && (
                <div className="space-y-1.5 sm:space-y-2">
                  <Label htmlFor="fullName" className="text-zinc-500 text-[10px] sm:text-xs font-mono uppercase tracking-wider">
                    Operator Name
                  </Label>
                  <Input
                    id="fullName"
                    data-testid="signup-name"
                    type="text"
                    placeholder="Enter callsign"
                    value={formData.fullName}
                    onChange={(e) => handleInputChange('fullName', e.target.value)}
                    className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-4 sm:py-5 font-mono text-sm"
                  />
                </div>
              )}
              
              <div className="space-y-1.5 sm:space-y-2">
                <Label htmlFor="email" className="text-zinc-500 text-[10px] sm:text-xs font-mono uppercase tracking-wider">
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
                  className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-4 sm:py-5 font-mono text-sm"
                />
              </div>
              
              <div className="space-y-1.5 sm:space-y-2">
                <Label htmlFor="password" className="text-zinc-500 text-[10px] sm:text-xs font-mono uppercase tracking-wider">
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
                  className="bg-zinc-900 border-zinc-800 text-white placeholder:text-zinc-700 focus:border-emerald-500/50 focus:ring-emerald-500/20 py-4 sm:py-5 font-mono text-sm"
                />
              </div>

              <Button
                type="submit"
                data-testid={isLogin ? "login-submit-btn" : "signup-submit-btn"}
                disabled={loading}
                className="w-full py-5 sm:py-6 text-xs sm:text-sm font-mono font-bold bg-emerald-500 hover:bg-emerald-400 text-black rounded border border-emerald-400 shadow-lg shadow-emerald-500/20 hover:shadow-emerald-400/40 transition-all duration-300 tracking-widest uppercase"
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
              onClick={handleDemoMode}
              data-testid="demo-mode-btn"
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
      <footer className="py-4 sm:py-6 px-4 border-t border-zinc-900 mb-16 sm:mb-0">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Eye className="w-4 h-4 text-zinc-700" />
            <span className="text-zinc-700 text-[10px] sm:text-xs font-mono">PROPVISION AI</span>
          </div>
          <span className="text-zinc-800 text-[10px] sm:text-xs font-mono">© 2026</span>
        </div>
      </footer>

      {/* Sticky Mobile CTA */}
      <div className="fixed bottom-0 left-0 right-0 p-3 bg-black/95 backdrop-blur-lg border-t border-zinc-800 sm:hidden z-50">
        <Button
          onClick={scrollToForm}
          className="w-full py-4 text-xs font-mono font-bold bg-emerald-500 hover:bg-emerald-400 text-black rounded border border-emerald-400 shadow-lg shadow-emerald-500/30 tracking-widest uppercase"
        >
          [ CLAIM YOUR EDGE ]
        </Button>
      </div>
    </div>
  );
};

export default Auth;
