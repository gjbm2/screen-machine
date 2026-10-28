// A bucket's maintenance dialog (the screen strip's More › Purge non-favourites…): purge the non-favourites (all, or
// those older than some days), re-index, extract the JSON. Moved unchanged out of BucketGridView so the Room page's
// screen cards open the same one (useScreenStripState.ts).

import { useState } from 'react';
import { toast } from 'sonner';
import { bucketsApi } from '@/shared/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export const BucketMaintenanceDialog = ({ isOpen, onClose, destination, onActionComplete }: { 
  isOpen: boolean;
  onClose: () => void;
  destination: string;
  onActionComplete: () => void;
}) => {
  const [days, setDays] = useState<number | undefined>();
  const [isLoading, setIsLoading] = useState(false);

  const handlePurge = async () => {
    setIsLoading(true);
    try {
      const result = await bucketsApi.purgeBucket(destination, days);
      if (result.status === 'purged') {
        const message = result.removed.length > 0 
          ? `Purged ${result.removed.length} files successfully`
          : 'No files were purged';
        toast.success(message);
        onActionComplete();
        onClose();
      } else {
        toast.error(result.error || 'Failed to purge files');
      }
    } catch (error) {
      console.error('Error purging files:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to purge files');
    } finally {
      setIsLoading(false);
    }
  };

  const handleReindex = async () => {
    setIsLoading(true);
    try {
      const success = await bucketsApi.performBucketMaintenance(destination, 'reindex');
      if (success) {
        toast.success('Bucket reindexed successfully');
        onActionComplete();
        onClose();
      }
    } catch (error) {
      console.error('Error reindexing bucket:', error);
      toast.error('Failed to reindex bucket');
    } finally {
      setIsLoading(false);
    }
  };

  const handleExtractJson = async () => {
    setIsLoading(true);
    try {
      const success = await bucketsApi.performBucketMaintenance(destination, 'extract');
      if (success) {
        toast.success('JSON extracted successfully');
        onActionComplete();
        onClose();
      }
    } catch (error) {
      console.error('Error extracting JSON:', error);
      toast.error('Failed to extract JSON');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bucket Maintenance</DialogTitle>
          <DialogDescription>
            Perform maintenance operations on this bucket
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Purge Non-Favorites</Label>
            <div className="flex items-center space-x-2">
              <Input
                type="number"
                placeholder="Days (optional)"
                value={days || ''}
                onChange={(e) => setDays(e.target.value ? parseInt(e.target.value) : undefined)}
                min={1}
              />
              <Button 
                onClick={handlePurge} 
                disabled={isLoading}
                variant="destructive"
              >
                Purge
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              {days ? `Will remove non-favorite files older than ${days} days` : 'Will remove all non-favorite files'}
            </p>
          </div>
          <div className="space-y-2">
            <Label>Reindex Bucket</Label>
            <Button 
              onClick={handleReindex} 
              disabled={isLoading}
              variant="outline"
            >
              Reindex
            </Button>
          </div>
          <div className="space-y-2">
            <Label>Extract JSON</Label>
            <Button 
              onClick={handleExtractJson} 
              disabled={isLoading}
              variant="outline"
            >
              Extract
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
