import { Switch, Route, Router } from "wouter";
import { useHashLocation } from "wouter/use-hash-location";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import { Toaster } from "@/components/ui/toaster";
import { AuthContext, useAuthProvider } from "./hooks/use-auth";
import LoginPage from "./pages/login";
import Dashboard from "./pages/dashboard";
import AdminPage from "./pages/admin";
import ProjectPage from "./pages/project";
import NotFound from "./pages/not-found";
import { Loader2 } from "lucide-react";

function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuthProvider();

  if (auth.loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!auth.user) {
    return (
      <AuthContext.Provider value={auth}>
        <LoginPage />
      </AuthContext.Provider>
    );
  }

  return (
    <AuthContext.Provider value={auth}>
      {children}
    </AuthContext.Provider>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <Router hook={useHashLocation}>
          <Switch>
            <Route path="/" component={Dashboard} />
            <Route path="/admin" component={AdminPage} />
            <Route path="/project/:id" component={ProjectPage} />
            <Route component={NotFound} />
          </Switch>
        </Router>
      </AuthGate>
      <Toaster />
    </QueryClientProvider>
  );
}

export default App;
