
import React from 'react';
import Header from '@/components/Header';
import { Button } from '@/components/ui/button';
import { Terminal } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface HeaderSectionProps {
  onToggleConsole: () => void;
  isConsoleVisible: boolean;
}

const HeaderSection: React.FC<HeaderSectionProps> = ({ 
  onToggleConsole,
  isConsoleVisible
}) => {
  return (
    <div className="flex justify-between items-center">
      <Header />
      <div className="flex items-center space-x-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button 
              variant="ghost" 
              size="icon" 
              onClick={onToggleConsole}
              className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50 mr-2"
            >
              <Terminal className={`h-5 w-5 ${isConsoleVisible ? 'text-primary' : ''}`} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Toggle Command Console</p>
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
};

export default HeaderSection;
