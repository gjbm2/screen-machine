
import React, { useState, useRef } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractImageMetadata } from "@/components/display/utils";
import { toast } from "sonner";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Upload, FileUp, RefreshCw, ExternalLink, Copy } from "lucide-react";

const Metadata = () => {
  const [imageUrl, setImageUrl] = useState<string>('');
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      // Create a local URL for the uploaded file
      const localUrl = URL.createObjectURL(file);
      setImageUrl(localUrl);
      setPreviewUrl(localUrl);
      
      console.log("Extracting metadata from uploaded file:", file.name);
      
      // Special handling for local files to get more metadata
      // This uses our existing function but with some extra info
      const extractedMetadata = await extractImageMetadata(localUrl);
      
      // Add file metadata that we can access directly
      const enhancedMetadata = {
        ...extractedMetadata,
        'filename': file.name,
        'filesize': formatFileSize(file.size),
        'lastModified': new Date(file.lastModified).toLocaleString(),
        'type': file.type,
      };
      
      console.log("File metadata extraction result:", enhancedMetadata);
      
      if (Object.keys(enhancedMetadata).length <= 4) {
        // If we only have the basic file properties we just added
        setError("Limited metadata extracted from this image");
        toast.warning("Limited metadata could be extracted due to browser security restrictions");
      } else {
        setMetadata(enhancedMetadata);
        toast.success(`Found ${Object.keys(enhancedMetadata).length} metadata entries`);
      }
    } catch (err) {
      console.error("Error processing file:", err);
      setError(`Failed to process file: ${err instanceof Error ? err.message : String(err)}`);
      toast.error("Error processing file");
    } finally {
      setLoading(false);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  const handleCopyMetadata = () => {
    const metadataText = Object.entries(metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n');
    
    navigator.clipboard.writeText(metadataText)
      .then(() => toast.success("Metadata copied to clipboard"))
      .catch(() => toast.error("Failed to copy metadata"));
  };

  return (
    <div className="container mx-auto py-8 px-4 max-w-4xl">
      <h1 className="text-3xl font-bold mb-6">Image Metadata Extractor</h1>
      
      <Tabs defaultValue="url" className="mb-6">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="url">URL Input</TabsTrigger>
          <TabsTrigger value="upload">File Upload</TabsTrigger>
        </TabsList>
        
        <TabsContent value="url">
          <Card>
            <CardHeader>
              <CardTitle>Extract Metadata from URL</CardTitle>
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
                      {loading ? (
                        <>
                          <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                          Extracting...
                        </>
                      ) : (
                        "Extract Metadata"
                      )}
                    </Button>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
        
        <TabsContent value="upload">
          <Card>
            <CardHeader>
              <CardTitle>Upload Image for Metadata</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="grid gap-2">
                  <Label htmlFor="fileUpload">Image File</Label>
                  <div className="flex flex-col gap-2">
                    <Input
                      id="fileUpload"
                      type="file"
                      accept="image/*"
                      ref={fileInputRef}
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                    <div className="flex gap-2">
                      <Button 
                        onClick={() => fileInputRef.current?.click()}
                        variant="outline"
                        className="flex-1"
                      >
                        <Upload className="mr-2 h-4 w-4" />
                        Choose File
                      </Button>
                      <Button
                        onClick={() => {
                          if (fileInputRef.current) {
                            fileInputRef.current.value = '';
                            fileInputRef.current.click();
                          }
                        }}
                        disabled={loading}
                      >
                        <FileUp className="mr-2 h-4 w-4" />
                        Upload & Extract
                      </Button>
                    </div>
                    {imageUrl && imageUrl.startsWith('blob:') && (
                      <p className="text-xs text-muted-foreground mt-1">
                        Using local file: {metadata.filename || "Unknown file"}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
      
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
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle>Metadata Results</CardTitle>
              {Object.keys(metadata).length > 0 && (
                <Button variant="ghost" size="sm" onClick={handleCopyMetadata}>
                  <Copy className="h-4 w-4 mr-1" />
                  Copy
                </Button>
              )}
            </CardHeader>
            <CardContent>
              {error ? (
                <div className="text-red-500 p-4 rounded-md bg-red-50">
                  {error}
                  <p className="mt-2 text-sm">
                    For more complete metadata extraction, consider:
                  </p>
                  <ul className="list-disc ml-5 text-sm mt-1">
                    <li>Uploading the image directly instead of using a URL</li>
                    <li>Using images that contain rich metadata (like from professional cameras)</li>
                    <li>Some websites strip metadata from images for privacy reasons</li>
                  </ul>
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
        <p className="mb-2">
          This tool extracts all available metadata from images, including EXIF data when present.
          Browser security restrictions may limit extraction from remote URLs.
        </p>
        <p>
          For complete metadata extraction, consider using the file upload method or a server-side 
          solution that can use dedicated libraries like ExifTool.
        </p>
      </div>
    </div>
  );
};

export default Metadata;
