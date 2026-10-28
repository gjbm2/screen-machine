// Upload to a screen's bucket (the screen strip's Upload): a file from this device, or a picture's web address (the
// Camera tab is a placeholder). Moved unchanged out of BucketGridView so the Room page's screen cards open the same
// one; the caller re-reads what it shows in onUploaded.

import { useState } from 'react';
import { toast } from 'sonner';
import { Camera, Link, Upload } from 'lucide-react';
import { bucketsApi } from '@/shared/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export function BucketUploadDialog({
  open,
  onOpenChange,
  destination,
  name,
  onUploaded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: string;
  /** The screen's name, for the title (default: its id). */
  name?: string;
  onUploaded?: () => void;
}) {
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadUrl, setUploadUrl] = useState('');
  const [uploadTab, setUploadTab] = useState<'file' | 'url'>('file');
  const hasCamera = 'mediaDevices' in navigator && 'getUserMedia' in navigator.mediaDevices;

  const handleUpload = async () => {
    if (!uploadFile && !uploadUrl) {
      toast.error('Please select a file or enter a URL');
      return;
    }

    try {
      if (uploadFile) {
        await bucketsApi.uploadToBucket(destination, uploadFile);
      } else if (uploadUrl) {
        await bucketsApi.uploadUrlToBucket(destination, uploadUrl);
      }

      onOpenChange(false);
      setUploadFile(null);
      setUploadUrl('');
      onUploaded?.();
      toast.success('Image uploaded successfully');
    } catch (error) {
      console.error('Error uploading image:', error);
      toast.error('Failed to upload image');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload to {name || destination}</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="file" value={uploadTab} onValueChange={(value) => setUploadTab(value as 'file' | 'url')}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="file" className="flex items-center justify-center">
              <Upload className="h-4 w-4 mr-2" />
              <span>File</span>
            </TabsTrigger>
            <TabsTrigger value="url" className="flex items-center justify-center">
              <Link className="h-4 w-4 mr-2" />
              <span>URL</span>
            </TabsTrigger>
            <TabsTrigger value="camera" disabled={!hasCamera} className="flex items-center justify-center">
              <Camera className="h-4 w-4 mr-2" />
              <span>Camera</span>
            </TabsTrigger>
          </TabsList>

          <TabsContent value="file" className="pt-4">
            <div className="grid gap-4">
              <Input
                id="file-upload"
                type="file"
                className="w-full"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setUploadFile(e.target.files[0]);
                  }
                }}
              />
              <p className="text-xs text-muted-foreground">
                Select a file from your device to upload.
              </p>
            </div>
          </TabsContent>

          <TabsContent value="url" className="pt-4">
            <div className="grid gap-4">
              <Input
                id="url-upload"
                type="url"
                placeholder="https://example.com/image.jpg"
                value={uploadUrl}
                onChange={(e) => setUploadUrl(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Enter the URL of an image to upload from the web.
              </p>
            </div>
          </TabsContent>

          <TabsContent value="camera" className="pt-4">
            <div className="grid gap-4">
              <div className="bg-black aspect-video rounded-md flex items-center justify-center">
                <p className="text-white">Camera access coming soon</p>
              </div>
              <p className="text-xs text-muted-foreground">
                Take a photo using your device's camera.
              </p>
            </div>
          </TabsContent>
        </Tabs>

        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" onClick={handleUpload}>
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
