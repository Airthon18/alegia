import { useEffect, useRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, useAuth } from "@/hooks/useAuth";

import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import Prediccion from "./pages/Prediccion";

const queryClient = new QueryClient();

const WelcomeVoice = () => {
  const { loading, profile, user } = useAuth();
  const hasSpokenRef = useRef(false);

  useEffect(() => {
    if (loading || hasSpokenRef.current) return;

    const speakWelcome = () => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) return;

      const rawName = profile?.full_name?.trim() || user?.email?.split("@")[0] || "usuario";
      const displayName = rawName.replace(/_/g, " ").replace(/\s+/g, " ").trim();
      const friendlyName = displayName.toLowerCase() === "invitado" ? "invitado" : displayName;
      const message = `Bienvenido, ${friendlyName}.`;

      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = "es-ES";
      utterance.rate = 1;
      utterance.pitch = 1.02;
      utterance.volume = 0.95;

      const voices = window.speechSynthesis.getVoices();
      const preferredVoice = voices.find((voice) => voice.lang.startsWith("es")) || voices[0];
      if (preferredVoice) {
        utterance.voice = preferredVoice;
      }

      window.speechSynthesis.cancel();
      window.speechSynthesis.resume();
      window.speechSynthesis.speak(utterance);
      hasSpokenRef.current = true;
    };

    const attemptSpeak = () => {
      if (hasSpokenRef.current) return;

      const voices = window.speechSynthesis.getVoices();
      if (voices.length > 0) {
        speakWelcome();
        return;
      }

      const onVoicesChanged = () => {
        speakWelcome();
      };

      window.speechSynthesis.addEventListener("voiceschanged", onVoicesChanged, { once: true });
      window.setTimeout(() => {
        if (!hasSpokenRef.current) {
          speakWelcome();
        }
      }, 800);
    };

    const handleFirstInteraction = () => {
      attemptSpeak();
    };

    const events = ["pointerdown", "keydown", "touchstart", "mousedown", "click"] as const;

    events.forEach((eventName) => {
      window.addEventListener(eventName, handleFirstInteraction, { once: true });
    });

    window.setTimeout(() => {
      if (!hasSpokenRef.current) {
        attemptSpeak();
      }
    }, 400);

    return () => {
      events.forEach((eventName) => {
        window.removeEventListener(eventName, handleFirstInteraction);
      });
    };
  }, [loading, profile, user]);

  return null;
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <AuthProvider>
        <WelcomeVoice />
        <Toaster />
        <Sonner />
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/prediccion" element={<Prediccion />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;