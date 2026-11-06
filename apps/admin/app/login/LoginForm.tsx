"use client";

import { ActionForm } from "@/components/ActionForm";
import { login } from "../actions";

export function LoginForm() {
  return (
    <ActionForm action={login} submit="Sign in" resetOnSuccess={false}>
      <label>
        E-mail
        <input type="text" name="email" autoComplete="username" required autoFocus />
      </label>
      <label>
        Password
        <input type="password" name="password" autoComplete="current-password" required />
      </label>
    </ActionForm>
  );
}
