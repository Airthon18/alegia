import { useState, useEffect } from "react";
import { useAuth } from "@/hooks/useAuth";
import {
  Home,
  MessageSquare,
  User,
  Newspaper,
  Users,
  LogOut,
  Zap,
  Menu,
  X,
  ChevronRight,
  Sparkles
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

type Section = "home" | "chat" | "account" | "news" | "admin" | "prediccion";

interface AppLayoutProps {
  children: (section: Section, setSection: (s: Section) => void) => React.ReactNode;
}

const navItems: { id: Section; label: string; icon: React.ElementType; adminOnly?: boolean }[] = [
  { id: "home",       label: "Inicio",              icon: Home },
  { id: "prediccion", label: "Predicción",          icon: Zap },
  { id: "account",    label: "Mi Cuenta & Plan",    icon: User },
  { id: "news",       label: "Noticias Deportivas", icon: Newspaper },
  { id: "admin",      label: "Gestión Interna",     icon: Users, adminOnly: true },
];

export function AppLayout({ children }: AppLayoutProps) {
  const { signOut, isAdmin, user } = useAuth();
  const [section, setSection] = useState<Section>("home");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const filteredNav = navItems.filter((item) => !item.adminOnly || isAdmin);

  const navigate = (id: Section) => {
    setSection(id);
    setMobileMenuOpen(false);
  };

  // Prevenir scroll cuando el menú móvil está abierto
  useEffect(() => {
    if (mobileMenuOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "unset";
    }
  }, [mobileMenuOpen]);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;700;800&family=Inter:wght@300;400;500;600&display=swap');

        :root {
          --bg-dark: #05050A;
          --glass-bg: rgba(255, 255, 255, 0.02);
          --glass-border: rgba(255, 255, 255, 0.05);
          --glass-hover: rgba(255, 255, 255, 0.06);
          --text-main: #F8FAFC;
          --text-muted: #94A3B8;
          --sidebar-w: 280px;
          --header-h: 70px;
        }

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        .alegia-root {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          background-color: var(--bg-dark);
          background-image: 
            radial-gradient(circle at 15% 50%, rgba(139, 92, 246, 0.08) 0px, transparent 40%),
            radial-gradient(circle at 85% 30%, rgba(217, 70, 239, 0.08) 0px, transparent 40%);
          background-attachment: fixed;
          font-family: 'Inter', sans-serif;
          color: var(--text-main);
        }

        /* -- HEADER -- */
        .glass-header {
          position: fixed;
          top: 0; left: 0; right: 0;
          height: var(--header-h);
          z-index: 50;
          background: rgba(5, 5, 10, 0.6);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border-bottom: 1px solid var(--glass-border);
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 16px;
          transition: all 0.3s ease;
        }

        @media (min-width: 768px) {
          .glass-header { padding: 0 24px; }
        }

        .brand-logo {
          display: flex;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          text-decoration: none;
        }

        .brand-icon {
          width: 38px; height: 38px;
          border-radius: 12px;
          background: linear-gradient(135deg, #8B5CF6 0%, #D946EF 100%);
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 0 20px rgba(217, 70, 239, 0.3);
          color: white;
        }

        .brand-text {
          font-family: 'Outfit', sans-serif;
          font-size: 1.4rem;
          font-weight: 800;
          letter-spacing: -0.02em;
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .header-actions {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        /* -- USER PILL -- */
        .user-pill {
          display: none;
          align-items: center;
          gap: 10px;
          background: var(--glass-bg);
          border: 1px solid var(--glass-border);
          border-radius: 99px;
          padding: 6px 16px 6px 6px;
          font-size: 0.8rem;
          font-weight: 500;
          color: var(--text-muted);
        }

        @media (min-width: 640px) { .user-pill { display: flex; } }

        .user-avatar {
          width: 28px; height: 28px;
          border-radius: 50%;
          background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(217, 70, 239, 0.2));
          border: 1px solid rgba(217, 70, 239, 0.3);
          display: flex; align-items: center; justify-content: center;
          font-size: 0.75rem; font-weight: 700; color: #E879F9;
        }

        /* -- BUTTONS -- */
        .btn-glass {
          display: flex; align-items: center; justify-content: center; gap: 8px;
          height: 40px;
          padding: 0 16px;
          border-radius: 12px;
          background: var(--glass-bg);
          border: 1px solid var(--glass-border);
          color: var(--text-muted);
          font-family: 'Inter', sans-serif;
          font-size: 0.85rem; font-weight: 500;
          cursor: pointer;
          transition: all 0.2s ease;
        }

        .btn-glass:hover {
          background: var(--glass-hover);
          color: var(--text-main);
        }

        .btn-logout { padding: 0 12px; }
        .btn-logout:hover {
          background: rgba(239, 68, 68, 0.1);
          border-color: rgba(239, 68, 68, 0.2);
          color: #F87171;
        }

        .logout-text { display: none; }
        @media (min-width: 640px) { .logout-text { display: inline; } }

        .btn-menu {
          width: 40px; padding: 0;
        }
        @media (min-width: 1024px) { .btn-menu { display: none; } }

        /* -- LAYOUT BODY -- */
        .app-body {
          display: flex;
          flex: 1;
          padding-top: var(--header-h);
        }

        /* -- SIDEBAR DESKTOP -- */
        .glass-sidebar {
          display: none;
          position: fixed;
          left: 0; top: var(--header-h); bottom: 0;
          width: var(--sidebar-w);
          background: rgba(5, 5, 10, 0.3);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border-right: 1px solid var(--glass-border);
          flex-direction: column;
          z-index: 30;
        }

        @media (min-width: 1024px) { .glass-sidebar { display: flex; } }

        .sidebar-header {
          padding: 24px 20px 16px;
        }

        .status-badge {
          display: inline-flex; align-items: center; gap: 8px;
          background: rgba(139, 92, 246, 0.1);
          border: 1px solid rgba(139, 92, 246, 0.2);
          border-radius: 99px;
          padding: 6px 14px;
          font-family: 'Outfit', sans-serif;
          font-size: 0.7rem; font-weight: 600;
          letter-spacing: 0.05em; text-transform: uppercase;
          color: #A78BFA;
        }

        .status-dot {
          width: 6px; height: 6px;
          border-radius: 50%;
          background: #A78BFA;
          box-shadow: 0 0 8px #A78BFA;
          animation: pulse-dot 2s infinite;
        }

        @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }

        /* -- NAVIGATION ITEMS -- */
        .nav-menu { 
          flex: 1; padding: 10px 16px; 
          display: flex; flex-direction: column; gap: 6px; 
          overflow-y: auto;
        }

        .nav-item {
          display: flex; align-items: center; gap: 14px;
          padding: 14px 16px;
          border-radius: 14px;
          font-size: 0.9rem; font-weight: 500;
          cursor: pointer;
          border: 1px solid transparent;
          background: transparent;
          width: 100%;
          text-align: left;
          color: var(--text-muted);
          transition: all 0.2s ease;
        }

        .nav-item:hover {
          color: var(--text-main);
          background: var(--glass-hover);
        }

        .nav-item.active {
          color: white;
          background: linear-gradient(90deg, rgba(139, 92, 246, 0.15) 0%, rgba(217, 70, 239, 0.05) 100%);
          border-color: rgba(217, 70, 239, 0.2);
          box-shadow: inset 2px 0 0 #D946EF;
        }

        .nav-icon { flex-shrink: 0; transition: color 0.2s; color: var(--text-muted); }
        .nav-item.active .nav-icon { color: #E879F9; }
        
        .nav-chevron { margin-left: auto; opacity: 0; color: #E879F9; transition: opacity 0.2s; }
        .nav-item.active .nav-chevron { opacity: 1; }

        /* -- SIDEBAR FOOTER -- */
        .sidebar-footer {
          padding: 20px;
          border-top: 1px solid var(--glass-border);
        }

        .user-card {
          display: flex; align-items: center; gap: 12px;
          padding: 12px;
          border-radius: 16px;
          background: var(--glass-bg);
          border: 1px solid var(--glass-border);
        }

        .user-card-info { flex: 1; overflow: hidden; }
        .user-card-name {
          font-size: 0.85rem; font-weight: 600;
          color: var(--text-main);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .user-card-role {
          font-size: 0.7rem; color: #A78BFA; margin-top: 2px; font-weight: 500;
        }

        /* -- MOBILE MENU (DRAWER) -- */
        .mobile-overlay {
          position: fixed; inset: 0; z-index: 45;
          background: rgba(0, 0, 0, 0.6);
          backdrop-filter: blur(6px);
          -webkit-backdrop-filter: blur(6px);
        }

        .mobile-drawer {
          position: fixed;
          top: var(--header-h); left: 0; bottom: 0;
          width: min(300px, 85vw);
          background: rgba(10, 10, 15, 0.85);
          backdrop-filter: blur(24px);
          -webkit-backdrop-filter: blur(24px);
          border-right: 1px solid var(--glass-border);
          z-index: 46;
          display: flex; flex-direction: column;
          box-shadow: 20px 0 40px rgba(0,0,0,0.5);
        }

        /* -- MAIN CONTENT AREA -- */
        .app-main {
          flex: 1;
          overflow-y: auto;
          min-height: calc(100vh - var(--header-h));
          display: flex; flex-direction: column;
        }

        .app-main.fullscreen { overflow: hidden; }

        @media (min-width: 1024px) {
          .app-main { margin-left: var(--sidebar-w); }
        }

        .app-footer {
          padding: 24px;
          text-align: center;
          font-size: 0.75rem;
          color: var(--text-muted);
          margin-top: auto;
        }
        .app-footer span { 
          background: linear-gradient(135deg, #A78BFA 0%, #E879F9 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          font-weight: 700; 
          font-family: 'Outfit', sans-serif;
        }
      `}</style>

      <div className="alegia-root">
        {/* TOP HEADER */}
        <header className="glass-header">
          <div className="brand-logo" onClick={() => navigate("home")}>
            <div className="brand-icon">
              <Sparkles size={20} />
            </div>
            <span className="brand-text">alegIA</span>
          </div>

          <div className="header-actions">
            <div className="user-pill">
              <div className="user-avatar">
                {user?.email?.charAt(0).toUpperCase()}
              </div>
              <span className="truncate max-w-[120px]">{user?.email}</span>
            </div>

            <button className="btn-glass btn-logout" onClick={signOut} title="Cerrar sesión">
              <LogOut size={16} />
              <span className="logout-text">Salir</span>
            </button>

            <button
              className="btn-glass btn-menu"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              aria-label="Menú"
            >
              {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </header>

        <div className="app-body">
          {/* SIDEBAR DESKTOP */}
          <aside className="glass-sidebar">
            <div className="sidebar-header">
              <div className="status-badge">
                <span className="status-dot" />
                Motor IA En Línea
              </div>
            </div>

            <nav className="nav-menu">
              {filteredNav.map((item) => {
                const Icon = item.icon;
                const active = section === item.id;
                return (
                  <button
                    key={item.id}
                    className={`nav-item ${active ? "active" : ""}`}
                    onClick={() => navigate(item.id)}
                  >
                    <Icon size={18} className="nav-icon" />
                    {item.label}
                    <ChevronRight size={16} className="nav-chevron" />
                  </button>
                );
              })}
            </nav>

            <div className="sidebar-footer">
              <div className="user-card">
                <div className="user-avatar" style={{ width: 36, height: 36, fontSize: '1rem' }}>
                  {user?.email?.charAt(0).toUpperCase()}
                </div>
                <div className="user-card-info">
                  <div className="user-card-name">{user?.email}</div>
                  <div className="user-card-role">{isAdmin ? "Admin Supremo" : "Usuario Pro"}</div>
                </div>
              </div>
            </div>
          </aside>

          {/* MOBILE MENU (DRAWER ANIMADO) */}
          <AnimatePresence>
            {mobileMenuOpen && (
              <>
                <motion.div
                  className="mobile-overlay"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  onClick={() => setMobileMenuOpen(false)}
                />
                <motion.div
                  className="mobile-drawer"
                  initial={{ x: "-100%" }}
                  animate={{ x: 0 }}
                  exit={{ x: "-100%" }}
                  transition={{ type: "spring", damping: 25, stiffness: 250 }}
                >
                  <div className="sidebar-header pt-6">
                    <div className="status-badge">
                      <span className="status-dot" />
                      Motor IA En Línea
                    </div>
                  </div>
                  
                  <nav className="nav-menu mt-4">
                    {filteredNav.map((item) => {
                      const Icon = item.icon;
                      const active = section === item.id;
                      return (
                        <button
                          key={item.id}
                          className={`nav-item ${active ? "active" : ""}`}
                          onClick={() => navigate(item.id)}
                        >
                          <Icon size={20} className="nav-icon" />
                          <span className="text-base">{item.label}</span>
                          <ChevronRight size={18} className="nav-chevron" />
                        </button>
                      );
                    })}
                  </nav>

                  <div className="sidebar-footer mt-auto">
                    <div className="user-card">
                      <div className="user-avatar" style={{ width: 40, height: 40, fontSize: '1.2rem' }}>
                        {user?.email?.charAt(0).toUpperCase()}
                      </div>
                      <div className="user-card-info">
                        <div className="user-card-name text-sm">{user?.email}</div>
                        <div className="user-card-role text-xs">{isAdmin ? "Admin Supremo" : "Usuario Pro"}</div>
                      </div>
                    </div>
                  </div>
                </motion.div>
              </>
            )}
          </AnimatePresence>

          {/* MAIN CONTENT */}
          <main className={`app-main ${section === "chat" || section === "prediccion" ? "fullscreen" : ""}`}>
            <AnimatePresence mode="wait">
              <motion.div
                key={section}
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.2 }}
                style={{ 
                  flex: 1, 
                  display: "flex", 
                  flexDirection: "column",
                  height: section === "chat" || section === "prediccion" ? "calc(100vh - 70px)" : "auto" 
                }}
              >
                {children(section, setSection)}
              </motion.div>
            </AnimatePresence>

            {section !== "chat" && section !== "prediccion" && (
              <footer className="app-footer">
                © {new Date().getFullYear()} <span>alegIA</span> – La ventaja inteligente. Todos los derechos reservados.
              </footer>
            )}
          </main>
        </div>
      </div>
    </>
  );
}