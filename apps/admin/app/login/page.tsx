import { LoginForm } from "./LoginForm";

export const metadata = { title: "Sign in - Mabeyn Admin" };

export default function LoginPage() {
  return (
    <div className="login">
      <div className="card stack">
        <div>
          <h1>Mabeyn Admin</h1>
          <p className="muted">Sign in to manage your support assistant.</p>
        </div>
        <LoginForm />
      </div>
    </div>
  );
}
