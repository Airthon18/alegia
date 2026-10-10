import { useEffect, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Search, Trash2, Eye, Edit } from "lucide-react";
import { toast } from "sonner";

interface UserRow {
  user_id: string;
  full_name: string;
  plan: string;
  created_at: string;
  email?: string;
}

export default function Admin() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    const { data, error } = await supabase.from("profiles").select("*");
    if (error) {
      toast.error("Error al cargar usuarios");
      console.error(error);
    } else {
      setUsers(data || []);
    }
    setLoading(false);
  };

  const filtered = users.filter(
    (u) =>
      u.full_name.toLowerCase().includes(search.toLowerCase()) ||
      (u.email || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="p-6 lg:p-10 fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Gestión de Usuarios</h1>
          <p className="text-muted-foreground">Panel de administración</p>
        </div>
        <div className="flex gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar usuario..."
              className="pl-9 bg-muted border-border text-foreground w-64"
            />
          </div>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                <th className="text-left text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">ID</th>
                <th className="text-left text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">Nombre</th>
                <th className="text-left text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">Plan</th>
                <th className="text-left text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">Fecha registro</th>
                <th className="text-left text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">Estado</th>
                <th className="text-right text-xs text-muted-foreground font-medium uppercase tracking-wider px-6 py-3">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="text-center py-12 text-muted-foreground">Cargando...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={6} className="text-center py-12 text-muted-foreground">No se encontraron usuarios</td></tr>
              ) : (
                filtered.map((u) => (
                  <tr key={u.user_id} className="border-b border-border hover:bg-muted/30 transition-colors">
                    <td className="px-6 py-4 text-xs text-muted-foreground font-mono">{u.user_id.slice(0, 8)}...</td>
                    <td className="px-6 py-4 text-sm text-foreground font-medium">{u.full_name || "Sin nombre"}</td>
                    <td className="px-6 py-4">
                      <span className={`text-xs px-2 py-1 rounded-full font-medium ${
                        u.plan === "pro" ? "bg-success/20 text-success" : "bg-muted text-muted-foreground"
                      }`}>
                        {u.plan === "pro" ? "Pro" : "Free"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-muted-foreground">
                      {new Date(u.created_at).toLocaleDateString("es-ES")}
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-xs px-2 py-1 rounded-full bg-success/20 text-success font-medium">Activo</span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
