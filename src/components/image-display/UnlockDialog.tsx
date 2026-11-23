// The PIN that opens the hidden buckets (Private) for 30 minutes (routes/auth/lock.py; owner, 23 Nov). Reached by a
// long press on the Cloud tab, admins only. The first PIN ever typed becomes the PIN.
import { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { unlockHidden } from '@/shared/auth/viewer';

export function UnlockDialog({ open, choosing, onClose, onUnlocked }: {
  open: boolean;
  /** No PIN yet: this one becomes it. */
  choosing: boolean;
  onClose: () => void;
  onUnlocked: () => void;
}) {
  const [pin, setPin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const close = () => {
    setPin('');
    setError(null);
    onClose();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unlockHidden(pin);
      setPin('');
      onUnlocked();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not unlock');
      setPin('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-w-xs">
        <DialogHeader>
          <DialogTitle>{choosing ? 'Choose a PIN' : 'Unlock'}</DialogTitle>
          <DialogDescription>
            {choosing ? 'At least 4 characters. You will type it to unlock from now on.' : 'Opens for 30 minutes.'}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <Input
            type="password"
            inputMode="numeric"
            autoComplete="off"
            autoFocus
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            aria-label="PIN"
            className="text-center text-lg tracking-[0.4em]"
          />
          {error && <p className="text-sm text-rose-700">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy || pin.trim().length < 4}>
            {choosing ? 'Set PIN and unlock' : 'Unlock'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
