import { useAuth } from "@/hooks/useAuth";
import { AuthModal } from "@/components/AuthModal";
import { AppLayout } from "@/components/AppLayout";
import Home from "@/pages/Home";
import Account from "@/pages/Account";
import News from "@/pages/News";
import Admin from "@/pages/Admin";
import Prediccion from "@/pages/Prediccion";

export default function Index() {
  const { user, loading, bypassAuth } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Muestra login solo si no hay usuario Y no está en modo invitado
  // (es decir, cerró sesión manualmente)
  if (!user && !bypassAuth) {
    return <AuthModal />;
  }

  return (
    <AppLayout>
      {(section, setSection) => {
        switch (section) {
          case "home":
            return <Home onNavigateChat={() => setSection("chat")} />;
          case "account":
            return <Account />;
          case "news":
            return <News />;
          case "admin":
            return <Admin />;
          case "prediccion":
            return <Prediccion />;
          default:
            return <Home onNavigateChat={() => setSection("chat")} />;
        }
      }}
    </AppLayout>
  );
}