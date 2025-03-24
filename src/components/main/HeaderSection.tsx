
import React from 'react';
import { Link } from 'react-router-dom';
import Header from '@/components/Header';
import { Button } from '@/components/ui/button';
import { Terminal, Menu, Info, FileText } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import scriptsData from '@/data/scripts.json';

interface HeaderSectionProps {
  onToggleConsole: () => void;
  isConsoleVisible: boolean;
  onOpenAdvancedOptions?: () => void;
  onOpenAboutDialog?: () => void;
  onRunScript?: (scriptFilename: string) => void;
}

const HeaderSection: React.FC<HeaderSectionProps> = ({ 
  onToggleConsole,
  isConsoleVisible,
  onOpenAdvancedOptions,
  onOpenAboutDialog,
  onRunScript
}) => {
  const handleRunScript = (filename: string) => {
    if (onRunScript) {
      onRunScript(filename);
    }
  };

  return (
    <div className="flex justify-between items-center">
      <Header onOpenAboutDialog={onOpenAboutDialog} />
      
      <div className="flex items-center space-x-1">
        {/* Only show console button outside the menu if visible */}
        {isConsoleVisible && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon" 
                onClick={onToggleConsole}
                className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50 mr-2"
              >
                <Terminal className="h-5 w-5 text-primary" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Toggle Command Console</p>
            </TooltipContent>
          </Tooltip>
        )}
        
        {/* Burger menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button 
              variant="ghost" 
              size="icon" 
              className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            {!isConsoleVisible && (
              <DropdownMenuItem onClick={onToggleConsole}>
                <Terminal className="h-4 w-4 mr-2" />
                <span>Console View</span>
              </DropdownMenuItem>
            )}
            
            {onOpenAdvancedOptions && (
              <DropdownMenuItem onClick={onOpenAdvancedOptions}>
                <Info className="h-4 w-4 mr-2" />
                <span>Advanced</span>
              </DropdownMenuItem>
            )}
            
            <DropdownMenuItem onClick={onOpenAboutDialog}>
              <Info className="h-4 w-4 mr-2" />
              <span>About</span>
            </DropdownMenuItem>
            
            <DropdownMenuItem asChild>
              <a href="/display" target="_blank" rel="noopener noreferrer">
                <FileText className="h-4 w-4 mr-2" />
                <span>Display Editor</span>
              </a>
            </DropdownMenuItem>
            
            <DropdownMenuSeparator />
            
            {/* Scripts section */}
            {scriptsData.length > 0 && (
              <>
                <div className="px-2 py-1.5 text-sm font-semibold">Scripts</div>
                {scriptsData.map((script) => (
                  <DropdownMenuItem 
                    key={script.id}
                    onClick={() => handleRunScript(script.filename)}
                  >
                    <FileText className="h-4 w-4 mr-2" />
                    <span>{script.title}</span>
                  </DropdownMenuItem>
                ))}
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
};

export default HeaderSection;
