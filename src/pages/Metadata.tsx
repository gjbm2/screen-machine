
import React, { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { extractImageMetadata } from '@/components/display/utils';
import { toast } from 'sonner';
import { Loader2, Image, FileImage } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';

const MetadataPage = () => {
  const [url, setUrl] = useState<string>('');
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  const handleExtractMetadata = async () => {
    if (!url.trim()) {
      toast.error('Please enter an image URL');
      return;
    }

    setLoading(true);
    setMetadata({});
    
    try {
      // Save the URL to display the image
      setImageUrl(url);
      
      // Extract metadata with cache busting
      const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}cacheBust=${Date.now()}_${Math.random()}`;
      console.log('Extracting metadata from:', cacheBustUrl);
      
      toast.info('Extracting metadata...', { duration: 3000 });
      const extractedMetadata = await extractImageMetadata(cacheBustUrl);
      
      console.log('Metadata extraction result:', extractedMetadata);
      
      if (extractedMetadata && Object.keys(extractedMetadata).length > 0) {
        setMetadata(extractedMetadata);
        toast.success(`Found ${Object.keys(extractedMetadata).length} metadata entries`);
      } else {
        toast.warning('No metadata found in this image');
        setMetadata({ 'info': 'No metadata found in this image' });
      }
    } catch (error) {
      console.error('Error extracting metadata:', error);
      toast.error('Failed to extract metadata');
      setMetadata({ 'error': String(error) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto p-4 max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">Image Metadata Extractor</h1>
      
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Enter Image URL</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Input
              type="text"
              placeholder="https://example.com/image.jpg"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="flex-1"
            />
            <Button onClick={handleExtractMetadata} disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Extracting...
                </>
              ) : (
                'Extract Metadata'
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Image Preview */}
        <Card>
          <CardHeader>
            <CardTitle>Image Preview</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-center p-4 min-h-[300px]">
            {imageUrl ? (
              <div className="relative w-full aspect-square max-h-[400px] rounded-md overflow-hidden">
                <img
                  src={imageUrl}
                  alt="Image preview"
                  className="object-contain w-full h-full"
                  onError={() => {
                    toast.error('Failed to load image');
                    setImageUrl(null);
                  }}
                />
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center text-gray-400">
                <FileImage className="h-16 w-16 mb-2" />
                <p>Enter an image URL to see preview</p>
              </div>
            )}
          </CardContent>
        </Card>
        
        {/* Metadata Display */}
        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[400px] rounded-md border p-4">
              {Object.keys(metadata).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(metadata).map(([key, value], index) => (
                    <div key={index} className="rounded-lg bg-gray-50 p-3">
                      <div className="font-medium text-sm">{key}</div>
                      <div className="text-xs text-gray-500 break-words">{value}</div>
                    </div>
                  ))}
                </div>
              ) : loading ? (
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-gray-400">
                  <Image className="h-8 w-8 mb-2" />
                  <p className="text-center">
                    {imageUrl ? 'No metadata found' : 'Enter an image URL to extract metadata'}
                  </p>
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default MetadataPage;
