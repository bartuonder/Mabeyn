"use client";

import { startTransition, useActionState, useEffect, useRef, type FormEvent, type ReactNode } from "react";

type State = { ok?: string; error?: string; secret?: string };

// React clears a form once its action finishes, which would also wipe what the user typed when the
// server rejects it. The submit is handled by hand instead: the fields stay after an error and are
// cleared only after a success (unless the form edits saved settings).
export function ActionForm({
  action,
  submit,
  children,
  className = "form",
  resetOnSuccess = true,
}: {
  action: (previous: State, data: FormData) => Promise<State>;
  submit?: string;
  children: ReactNode;
  className?: string;
  resetOnSuccess?: boolean;
}) {
  const [state, formAction, pending] = useActionState(action, {});
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (state.ok && resetOnSuccess) form.current?.reset();
  }, [state, resetOnSuccess]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLElement | null;
    const data = new FormData(event.currentTarget, submitter);
    startTransition(() => formAction(data));
  }

  return (
    <form ref={form} onSubmit={onSubmit} className={className} aria-busy={pending}>
      {children}
      {submit && (
        <div>
          <button type="submit" disabled={pending}>
            {pending ? "Working..." : submit}
          </button>
        </div>
      )}
      {state.error && <p className="error" role="alert">{state.error}</p>}
      {state.ok && <p className="ok" role="status">{state.ok}</p>}
      {state.secret && (
        <p className="secret secret-box" role="status">
          Copy this now, it is shown only once: <code>{state.secret}</code>
        </p>
      )}
    </form>
  );
}
