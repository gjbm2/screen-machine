
import React, { useState } from 'react';
import { toast } from 'sonner';
import Header from '@/components/Header';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/ImageDisplay';

const Index = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrl, setUploadedImageUrl] = useState<string | null>(null);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);

  const handleSubmitPrompt = async (
    prompt: string, 
    imageFile?: File | null,
    workflow?: string,
    params?: Record<string, any>
  ) => {
    setIsLoading(true);
    setCurrentPrompt(prompt);
    setCurrentWorkflow(workflow || null);
    
    // If user uploaded an image, create a local URL for display
    if (imageFile) {
      const localImageUrl = URL.createObjectURL(imageFile);
      setUploadedImageUrl(localImageUrl);
    } else {
      setUploadedImageUrl(null);
    }
    
    try {
      // In a real implementation, you would send both the prompt and image file
      // to your backend using FormData
      const formData = new FormData();
      if (prompt) formData.append('prompt', prompt);
      if (imageFile) formData.append('image', imageFile);
      if (workflow) formData.append('workflow', workflow);
      if (params) {
        // Convert params object to a JSON string and append to FormData
        formData.append('params', JSON.stringify(params));
      }
      
      // For the mock implementation, we'll just use the existing endpoint
      const response = await fetch('http://localhost:5000/generate-image', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          prompt,
          workflow: workflow || 'text-to-image',
          params: params || {},
          has_reference_image: imageFile ? true : false 
        }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to generate image');
      }
      
      const data = await response.json();
      setImageUrl(data.image_url);
      toast.success('Image generated successfully!');
    } catch (error) {
      console.error('Error generating image:', error);
      toast.error('Failed to generate image. Please try again.');
      // Clear the imageUrl on error to avoid showing stale images
      setImageUrl(null);
    } finally {
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
            Describe anything you can imagine, or upload a reference image, and watch as AI transforms your ideas into stunning visuals in seconds.
          </p>
        </div>
        
        <div className="mt-12 max-w-2xl mx-auto">
          <PromptForm onSubmit={handleSubmitPrompt} isLoading={isLoading} />
        </div>
        
        {/* Always show the ImageDisplay component. It will handle displaying loading state or the image */}
        <div className="mb-20">
          <ImageDisplay 
            imageUrl={imageUrl}
            prompt={currentPrompt}
            isLoading={isLoading}
            uploadedImage={uploadedImageUrl}
            workflow={currentWorkflow}
          />
        </div>
      </div>
    </div>
  );
};

export default Index;
