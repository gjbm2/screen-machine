import React, { useState } from 'react';
import { toast } from 'sonner';
import Header from '@/components/Header';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/ImageDisplay';

const Index = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);
  
  // Function to handle using the generated image as input
  const handleUseGeneratedAsInput = async () => {
    if (!imageUrl) return;
    
    try {
      // Fetch the image to create a File object
      const response = await fetch(imageUrl);
      const blob = await response.blob();
      const file = new File([blob], 'generated-image.png', { type: 'image/png' });
      
      // Clear all current state
      setCurrentPrompt('');
      
      // Add the generated image to the uploaded images and clear the current generated image
      const localImageUrl = URL.createObjectURL(file);
      setUploadedImageUrls([localImageUrl]);
      setImageUrl(null);
      
      // Switch workflow to image-to-image if not already
      setCurrentWorkflow('image-to-image');
      
      toast.success('Generated image added as input!');
    } catch (error) {
      console.error('Error using generated image as input:', error);
      toast.error('Failed to use image as input. Please try again.');
    }
  };

  const handleSubmitPrompt = async (
    prompt: string, 
    imageFiles?: File[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>
  ) => {
    setIsLoading(true);
    setCurrentPrompt(prompt);
    setCurrentWorkflow(workflow || null);
    
    // If user uploaded images, create local URLs for display
    if (imageFiles && imageFiles.length > 0) {
      const localImageUrls = imageFiles.map(file => URL.createObjectURL(file));
      setUploadedImageUrls(localImageUrls);
    } else {
      setUploadedImageUrls([]);
    }
    
    try {
      // In a production environment, you would use FormData for file uploads
      // For this mock implementation, we'll just pass the data as JSON
      const requestData = {
        prompt,
        workflow: workflow || 'text-to-image',
        params: params || {},
        global_params: globalParams || {},
        has_reference_images: imageFiles ? imageFiles.length > 0 : false,
        reference_image_count: imageFiles ? imageFiles.length : 0
      };
      
      console.log('Sending request with data:', requestData);
      
      // For testing purposes, let's add a mock response directly
      // instead of actually making the network request
      setTimeout(() => {
        // Mock successful response
        const mockImageUrls = [
          "https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=1974&auto=format&fit=crop",
          "https://images.unsplash.com/photo-1605979257913-1704eb7b6246?q=80&w=1770&auto=format&fit=crop",
          "https://images.unsplash.com/photo-1692891873526-61e7e87ea428?q=80&w=1780&auto=format&fit=crop",
          "https://images.unsplash.com/photo-1533134486753-c833f0ed4866?q=80&w=1770&auto=format&fit=crop"
        ];
        
        // Select a random image from the mock images
        const randomIndex = Math.floor(Math.random() * mockImageUrls.length);
        setImageUrl(mockImageUrls[randomIndex]);
        setIsLoading(false);
        toast.success('Image generated successfully!');
      }, 1500); // Simulate network delay
      
    } catch (error) {
      console.error('Error generating image:', error);
      toast.error('Failed to generate image. Please try again.');
      // Clear the imageUrl on error to avoid showing stale images
      setImageUrl(null);
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen hero-gradient">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
        <Header />
        
        <div className="mt-16 md:mt-24 text-center animate-fade-in">
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight">
            Turn your words into <span className="text-primary">art</span>
          </h1>
          <p className="mt-6 text-lg text-foreground/70 max-w-2xl mx-auto">
            Describe anything you can imagine, or upload reference images, and watch as AI transforms your ideas into stunning visuals in seconds.
          </p>
        </div>
        
        <div className="mt-12 max-w-2xl mx-auto">
          <PromptForm 
            onSubmit={handleSubmitPrompt} 
            isLoading={isLoading}
            currentPrompt={currentPrompt}
          />
        </div>
        
        <div className="mb-20">
          <ImageDisplay 
            imageUrl={imageUrl}
            prompt={currentPrompt}
            isLoading={isLoading}
            uploadedImages={uploadedImageUrls}
            workflow={currentWorkflow}
            onUseGeneratedAsInput={handleUseGeneratedAsInput}
          />
        </div>
      </div>
    </div>
  );
};

export default Index;
