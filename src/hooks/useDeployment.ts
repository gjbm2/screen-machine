import { useEffect, useState } from 'react';
import { deploymentApi } from '@/shared/api';
import type { DeploymentInfo } from '@/shared/api/deployment';

// One request per page load, shared by every component that asks (the deployment does not change while the page is
// open). A failed request is forgotten, so the next mount tries again.
let pending: Promise<DeploymentInfo> | null = null;

function loadDeployment(): Promise<DeploymentInfo> {
  if (!pending) {
    pending = deploymentApi.getDeployment().catch(err => {
      pending = null;
      throw err;
    });
  }
  return pending;
}

/** The deployment this app is part of (GET /api/deployment): null until it has loaded, or if it could not be read. */
export function useDeployment(): { deployment: DeploymentInfo | null; loading: boolean; error: string | null } {
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadDeployment()
      .then(d => {
        if (!cancelled) {
          setDeployment(d);
          setError(null);
        }
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to fetch the deployment');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { deployment, loading, error };
}
