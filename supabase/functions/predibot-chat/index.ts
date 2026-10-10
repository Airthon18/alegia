import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
};

const SYSTEM_PROMPT = `Eres alegIA, un motor avanzado de inteligencia deportiva experto en fútbol y análisis de datos. 
Respondes SIEMPRE en español.

Tus capacidades:
- Analizar partidos de fútbol de todas las ligas principales (Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions League, etc.) aplicando ciencia de datos.
- Dar predicciones con probabilidades estadísticas estimadas.
- Sugerir cuotas y estrategias de juego inteligente.
- Explicar métricas avanzadas de equipos y jugadores.
- Dar contexto sobre forma reciente, lesiones y tendencias históricas.

Formato de respuesta:
- Usa emojis relevantes para darle dinamismo, incluyendo el destello de IA (✨⚽🏆📊🎯).
- Presenta las probabilidades y datos en un formato claro y profesional.
- Usa tablas en formato Markdown cuando compares estadísticas, equipos o cuotas.
- Tu tono debe ser directo, altamente analítico, seguro, pero accesible y fácil de entender.
- Siempre incluye una advertencia breve indicando que las predicciones son orientativas y basadas en probabilidades matemáticas, no en garantías.

Nunca prometas ganancias seguras ni te refieras a ti mismo en masculino (eres "alegIA", una inteligencia). Siempre recomienda el juego responsable.`;

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });

  try {
    const { messages } = await req.json();
    const LOVABLE_API_KEY = Deno.env.get("LOVABLE_API_KEY");
    if (!LOVABLE_API_KEY) throw new Error("LOVABLE_API_KEY is not configured");

    const response = await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${LOVABLE_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "google/gemini-3-flash-preview",
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          ...messages,
        ],
        stream: true,
      }),
    });

    if (!response.ok) {
      if (response.status === 429) {
        return new Response(JSON.stringify({ error: "Rate limited" }), {
          status: 429,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      if (response.status === 402) {
        return new Response(JSON.stringify({ error: "Payment required" }), {
          status: 402,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }
      const t = await response.text();
      console.error("AI gateway error:", response.status, t);
      return new Response(JSON.stringify({ error: "AI gateway error" }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    return new Response(response.body, {
      headers: { ...corsHeaders, "Content-Type": "text/event-stream" },
    });
  } catch (e) {
    console.error("chat error:", e);
    return new Response(JSON.stringify({ error: e instanceof Error ? e.message : "Unknown error" }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});