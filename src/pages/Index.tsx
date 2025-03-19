
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
      // In a real implementation, you would send both the prompt and image files
      // to your backend using FormData
      const formData = new FormData();
      if (prompt) formData.append('prompt', prompt);
      if (imageFiles) {
        imageFiles.forEach((file, index) => {
          formData.append(`image_${index}`, file);
        });
      }
      if (workflow) formData.append('workflow', workflow);
      if (params) {
        // Convert params object to a JSON string and append to FormData
        formData.append('params', JSON.stringify(params));
      }
      if (globalParams) {
        // Convert global params object to a JSON string and append to FormData
        formData.append('global_params', JSON.stringify(globalParams));
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
          global_params: globalParams || {},
          has_reference_images: imageFiles ? imageFiles.length > 0 : false,
          reference_image_count: imageFiles ? imageFiles.length : 0
        }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to generate image');
      }
      
      const data = await response.json();
      setImageUrl(data.image_url);
      
      // Log the full request for debugging purposes
      console.log('Request params:', {
        prompt,
        workflow,
        workflowParams: params,
        globalParams,
        imageCount: imageFiles?.length || 0
      });
      
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
            Describe anything you can imagine, or upload reference images, and watch as AI transforms your ideas into stunning visuals in seconds.
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
            uploadedImages={uploadedImageUrls}
            workflow={currentWorkflow}
          />
        </div>
      </div>
    </div>
  );
};

export default Index;
