// "Sign in with Google" (docs/ACCOUNTS.md): Google's own button (Google Identity Services), whose ID token Flask
// checks and turns into the app's session cookie. Google only signs in on the https address, so over http (the TVs,
// the IP address) it says so instead.
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { signInWithGoogle, useViewer } from '@/shared/auth/viewer';

const GSI_SRC = 'https://accounts.google.com/gsi/client';

interface GoogleId {
  initialize(options: { client_id: string; callback: (r: { credential: string }) => void; ux_mode?: string }): void;
  renderButton(el: HTMLElement, options: Record<string, unknown>): void;
}
declare global {
  interface Window { google?: { accounts: { id: GoogleId } } }
}

let gsiLoading: Promise<void> | null = null;
function loadGsi(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve();
  gsiLoading ??= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = GSI_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { gsiLoading = null; reject(new Error('Google sign-in did not load')); };
    document.head.appendChild(s);
  });
  return gsiLoading;
}

export function GoogleSignIn({ width = 240 }: { width?: number }) {
  const viewer = useViewer();
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const https = typeof window !== 'undefined' && window.location.protocol === 'https:';
  const clientId = viewer.google_client_id;

  useEffect(() => {
    if (!https || !clientId || !ref.current) return;
    let cancelled = false;
    loadGsi()
      .then(() => {
        if (cancelled || !ref.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: async ({ credential }) => {
            try {
              await signInWithGoogle(credential);
              window.location.reload(); // every list on the page (Generated, jobs, screens) is per person
            } catch (e) {
              toast.error(e instanceof Error ? e.message : 'Sign-in failed');
            }
          },
        });
        window.google.accounts.id.renderButton(ref.current, { theme: 'outline', size: 'large', text: 'signin_with', width });
      })
      .catch(() => setFailed(true));
    return () => { cancelled = true; };
  }, [https, clientId, width]);

  if (!clientId) return null;
  if (!https) {
    return <p className="text-sm text-muted-foreground">Open the app at its https address to sign in.</p>;
  }
  if (failed) return <p className="text-sm text-muted-foreground">Google sign-in could not load.</p>;
  return <div ref={ref} />;
}
