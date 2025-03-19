
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';

interface PromptFormProps {
  onSubmit: (prompt: string) => void;
  isLoading: boolean;
}

const EXAMPLE_PROMPTS = [
  "A peaceful lakeside cabin at sunset with mountains in the background",
  "A futuristic cityscape with flying cars and neon lights",
  "A magical forest with glowing mushrooms and fairy lights",
  "An astronaut riding a horse on Mars"
];

const PromptForm = ({ onSubmit, isLoading }: PromptFormProps) => {
  const [prompt, setPrompt] = useState('');
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!prompt.trim()) {
      toast.error('Please enter a prompt');
      return;
    }
    
    onSubmit(prompt);
  };

  const handleExampleClick = (example: string) => {
    setPrompt(example);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.2 }}
    >
      <Card className="overflow-hidden glass border border-border/30">
        <form onSubmit={handleSubmit} className="p-1">
          <Textarea
            placeholder="Describe the image you want to create..."
            className="min-h-[120px] resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={isLoading}
          />
          <div className="px-4 pb-3">
            <p className="text-xs text-muted-foreground mb-2">Try an example:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_PROMPTS.map((example, index) => (
                <button
                  key={index}
                  type="button"
                  className="text-xs bg-secondary/50 hover:bg-secondary px-2 py-1 rounded-full text-foreground/70 transition-colors"
                  onClick={() => handleExampleClick(example)}
                >
                  {example.length > 30 ? `${example.slice(0, 30)}...` : example}
                </button>
              ))}
            </div>
          </div>
          <div className="flex justify-end p-3 pt-2">
            <Button 
              type="submit" 
              className="btn-shine rounded-full px-6 transition-all hover:shadow-md"
              disabled={isLoading}
            >
              {isLoading ? 'Generating...' : 'Generate'}
            </Button>
          </div>
        </form>
      </Card>
    </motion.div>
  );
};

export default PromptForm;
