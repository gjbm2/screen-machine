
import React, { useState } from 'react';
import { Card } from '@/components/ui/card';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from '@/components/ui/button';
import { Info, Save, Share2, Copy, FileInput } from 'lucide-react';
import { toast } from 'sonner';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
  uploadedImages?: string[];
  generatedImages?: Array<{
    url: string;
    prompt: string;
    workflow: string;
    timestamp: number;
    params?: Record<string, any>;
  }>;
  workflow?: string | null;
  onUseGeneratedAsInput?: (imageUrl: string) => void;
  onCreateAgain?: () => void;
  generationParams?: Record<string, any>;
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({ 
  imageUrl, 
  prompt, 
  isLoading,
  uploadedImages = [],
  generatedImages = [],
  workflow,
  onUseGeneratedAsInput,
  onCreateAgain,
  generationParams
}) => {
  // Always render the component when we have uploaded images or when we're loading
  // or when we have generated image results
  const shouldDisplay = isLoading || generatedImages.length > 0 || (uploadedImages && uploadedImages.length > 0);
  
  if (!shouldDisplay) return null;

  // Format workflow name for display (remove hyphens and capitalize)
  const formatWorkflowName = (name: string) => {
    return name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const handleSaveImage = async (imgUrl: string) => {
    try {
      // Fetch the image data
      const response = await fetch(imgUrl);
      const blob = await response.blob();
      
      // Create a temporary anchor element
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      
      // Set filename with date
      const date = new Date().toISOString().split('T')[0];
      link.download = `generated-image-${date}.png`;
      
      // Trigger download and clean up
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
      
      toast.success('Image saved successfully!');
    } catch (error) {
      console.error('Error saving image:', error);
      toast.error('Failed to save image. Please try again.');
    }
  };

  const handlePublish = (imgUrl: string) => {
    toast.success('Image published successfully!');
    // In a real app, we would implement publishing logic here
  };

  const handleCreateVariant = (imgUrl: string, originalPrompt: string) => {
    if (onCreateAgain) {
      onCreateAgain();
      toast.info('Creating a new variant...');
    }
  };

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col gap-6">
        {/* Reference images section */}
        {uploadedImages && uploadedImages.length > 0 && (
          <div>
            <h3 className="text-lg font-semibold mb-3">Reference Images</h3>
            <div className="overflow-hidden bg-secondary/20 rounded-lg">
              {uploadedImages.length === 1 ? (
                <div className="aspect-square overflow-hidden">
                  <img
                    src={uploadedImages[0]}
                    alt="Reference image"
                    className="w-full h-full object-contain"
                  />
                </div>
              ) : (
                <Carousel className="w-full">
                  <CarouselContent>
                    {uploadedImages.map((url, index) => (
                      <CarouselItem key={index} className="md:basis-1/2 lg:basis-1/3">
                        <div className="p-1">
                          <Card className="overflow-hidden">
                            <div className="aspect-square relative">
                              <img
                                src={url}
                                alt={`Reference image ${index + 1}`}
                                className="w-full h-full object-cover"
                              />
                            </div>
                          </Card>
                        </div>
                      </CarouselItem>
                    ))}
                  </CarouselContent>
                  <CarouselPrevious />
                  <CarouselNext />
                </Carousel>
              )}
            </div>
          </div>
        )}

        {/* Generated images section */}
        {(isLoading || generatedImages.length > 0) && (
          <div>
            <h3 className="text-lg font-semibold mb-3">Generated Images</h3>
            
            {isLoading ? (
              <div className="flex h-64 items-center justify-center bg-secondary/20 rounded-lg">
                <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                {prompt && (
                  <p className="text-sm text-center text-muted-foreground absolute mt-20">
                    Generating: {prompt}
                  </p>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {generatedImages.map((img, index) => (
                  <Card key={index} className="overflow-hidden group relative">
                    <div className="aspect-square relative">
                      <img
                        src={img.url}
                        alt={img.prompt || 'Generated image'}
                        className="w-full h-full object-cover"
                      />
                      
                      {/* Image controls overlay - only visible on hover/focus */}
                      <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity flex flex-col justify-center items-center">
                        <div className="flex flex-wrap justify-center gap-2 p-4">
                          {/* Info button */}
                          <Dialog>
                            <DialogTrigger asChild>
                              <Button variant="secondary" size="sm" className="bg-white/20 hover:bg-white/30">
                                <Info className="h-4 w-4" />
                              </Button>
                            </DialogTrigger>
                            <DialogContent>
                              <DialogHeader>
                                <DialogTitle>Image Generation Details</DialogTitle>
                                <DialogDescription>
                                  Information about this generated image.
                                </DialogDescription>
                              </DialogHeader>
                              <div className="space-y-2 mt-4">
                                <div>
                                  <h4 className="font-semibold">Prompt:</h4>
                                  <p className="text-sm text-muted-foreground">{img.prompt}</p>
                                </div>
                                {img.workflow && (
                                  <div>
                                    <h4 className="font-semibold">Workflow:</h4>
                                    <p className="text-sm text-muted-foreground">{formatWorkflowName(img.workflow)}</p>
                                  </div>
                                )}
                                {img.params && Object.keys(img.params).length > 0 && (
                                  <div>
                                    <h4 className="font-semibold">Parameters:</h4>
                                    <div className="text-sm text-muted-foreground">
                                      {Object.entries(img.params).map(([key, value]) => (
                                        <div key={key} className="flex justify-between">
                                          <span className="capitalize">{key.replace(/([A-Z])/g, ' $1').trim()}:</span>
                                          <span>{value?.toString()}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                            </DialogContent>
                          </Dialog>
                          
                          {/* New variant button */}
                          <Button 
                            variant="secondary" 
                            size="sm" 
                            className="bg-white/20 hover:bg-white/30"
                            onClick={() => handleCreateVariant(img.url, img.prompt)}
                          >
                            <Copy className="h-4 w-4" />
                          </Button>
                          
                          {/* Save button */}
                          <Button 
                            variant="secondary" 
                            size="sm" 
                            className="bg-white/20 hover:bg-white/30"
                            onClick={() => handleSaveImage(img.url)}
                          >
                            <Save className="h-4 w-4" />
                          </Button>
                          
                          {/* Publish button */}
                          <Button 
                            variant="secondary" 
                            size="sm" 
                            className="bg-white/20 hover:bg-white/30"
                            onClick={() => handlePublish(img.url)}
                          >
                            <Share2 className="h-4 w-4" />
                          </Button>
                          
                          {/* Use as input button */}
                          {onUseGeneratedAsInput && (
                            <Button 
                              variant="secondary" 
                              size="sm" 
                              className="bg-white/20 hover:bg-white/30"
                              onClick={() => onUseGeneratedAsInput && onUseGeneratedAsInput(img.url)}
                            >
                              <FileInput className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                        
                        {/* Prompt preview */}
                        <div className="absolute bottom-0 w-full bg-black/70 p-2">
                          <p className="text-xs text-white truncate">{img.prompt}</p>
                        </div>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default ImageDisplay;
