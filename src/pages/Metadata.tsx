
import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractImageMetadata } from "@/components/display/utils";
import { toast } from "sonner";
import { ScrollArea } from "@/components/ui/scroll-area";

const Metadata = () => {
  const [imageUrl, setImageUrl] = useState<string>('');
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const handleExtractMetadata = async () => {
    if (!imageUrl) {
      toast.error("Please enter an image URL");
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      // Add a cache-busting parameter to ensure fresh content
      const cacheBustedUrl = `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}cacheBust=${Date.now()}_${Math.random()}`;
      
      console.log("Extracting metadata from:", cacheBustedUrl);
      
      // Extract metadata using the existing utility function
      const extractedMetadata = await extractImageMetadata(cacheBustedUrl);
      
      console.log("Metadata extraction result:", extractedMetadata);
      
      if (Object.keys(extractedMetadata).length === 0) {
        setError("No metadata found in this image");
        toast.warning("No metadata could be extracted from this image");
      } else {
        setMetadata(extractedMetadata);
        toast.success(`Found ${Object.keys(extractedMetadata).length} metadata entries`);
      }
      
      // Set preview URL regardless of metadata extraction success
      setPreviewUrl(imageUrl);
      
    } catch (err) {
      console.error("Error extracting metadata:", err);
      setError(`Failed to extract metadata: ${err instanceof Error ? err.message : String(err)}`);
      toast.error("Error extracting metadata");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto py-8 px-4 max-w-4xl">
      <h1 className="text-3xl font-bold mb-6">Image Metadata Extractor</h1>
      
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Extract Image Metadata</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="imageUrl">Image URL</Label>
              <div className="flex gap-2">
                <Input
                  id="imageUrl"
                  placeholder="https://example.com/image.jpg"
                  value={imageUrl}
                  onChange={(e) => setImageUrl(e.target.value)}
                  className="flex-1"
                />
                <Button 
                  onClick={handleExtractMetadata} 
                  disabled={loading || !imageUrl}
                >
                  {loading ? "Extracting..." : "Extract Metadata"}
                </Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      
      {previewUrl && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <Card>
            <CardHeader>
              <CardTitle>Image Preview</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex justify-center">
                <img 
                  src={previewUrl} 
                  alt="Preview" 
                  className="max-h-[300px] max-w-full object-contain rounded-md" 
                  onError={() => {
                    setError("Failed to load image preview");
                    toast.error("Failed to load image preview");
                  }}
                />
              </div>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader>
              <CardTitle>Metadata Results</CardTitle>
            </CardHeader>
            <CardContent>
              {error ? (
                <div className="text-red-500 p-4 rounded-md bg-red-50">
                  {error}
                </div>
              ) : Object.keys(metadata).length === 0 ? (
                <div className="text-gray-500 flex flex-col items-center justify-center h-[200px]">
                  {loading ? (
                    <p>Extracting metadata...</p>
                  ) : (
                    <p>No metadata extracted yet</p>
                  )}
                </div>
              ) : (
                <ScrollArea className="h-[300px] pr-4">
                  <div className="space-y-2">
                    {Object.entries(metadata).map(([key, value]) => (
                      <div key={key} className="bg-slate-50 p-3 rounded-md">
                        <div className="font-medium text-sm">{key}</div>
                        <div className="text-xs text-gray-600 break-words">{value}</div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <div className="text-sm text-gray-500 text-center mt-8">
        <p>
          This tool extracts all available metadata from images, including EXIF data when present.
          Some image hosting services may strip metadata for privacy reasons.
        </p>
      </div>
    </div>
  );
};

export default Metadata;
