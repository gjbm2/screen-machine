
import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { menuItems } from '@/data/menu-items';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Menu, Code, Sliders, Bug, Info } from 'lucide-react';
import AboutDialog from './about/AboutDialog';

interface HeaderProps {
  onToggleConsole: () => void;
  onOpenAdvancedOptions: () => void;
  onToggleVerboseDebug: () => void;
  verboseDebugEnabled: boolean;
  onOpenAboutDialog?: () => void;
}

const Header: React.FC<HeaderProps> = ({ 
  onToggleConsole, 
  onOpenAdvancedOptions,
  onToggleVerboseDebug,
  verboseDebugEnabled,
  onOpenAboutDialog
}) => {
  const [aboutDialogOpen, setAboutDialogOpen] = useState(false);
  
  const handleOpenAboutDialog = () => {
    if (onOpenAboutDialog) {
      onOpenAboutDialog();
    } else {
      setAboutDialogOpen(true);
    }
  };

  return (
    <header className="border-b">
      <div className="container mx-auto flex justify-between items-center p-2">
        <div className="flex items-center space-x-4">
          <h1 className="text-xl font-bold">
            Image Generator
          </h1>
        </div>
        
        <div className="flex items-center space-x-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <Menu className="h-5 w-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Options</DropdownMenuLabel>
              <DropdownMenuSeparator />
              
              <DropdownMenuItem onClick={onOpenAdvancedOptions}>
                <Sliders className="mr-2 h-4 w-4" />
                <span>Advanced Options</span>
              </DropdownMenuItem>
              
              <DropdownMenuItem onClick={onToggleConsole}>
                <Code className="mr-2 h-4 w-4" />
                <span>Toggle Console</span>
              </DropdownMenuItem>
              
              <DropdownMenuItem onClick={onToggleVerboseDebug}>
                <Bug className="mr-2 h-4 w-4" />
                <span>{verboseDebugEnabled ? 'Disable' : 'Enable'} Verbose Debug</span>
              </DropdownMenuItem>
              
              <DropdownMenuSeparator />
              
              <DropdownMenuItem onClick={handleOpenAboutDialog}>
                <Info className="mr-2 h-4 w-4" />
                <span>About</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          
          {/* Controlled dialog when using onOpenAboutDialog */}
          {onOpenAboutDialog && (
            <AboutDialog 
              open={aboutDialogOpen} 
              onOpenChange={setAboutDialogOpen} 
            />
          )}
        </div>
      </div>
    </header>
  );
};

export default Header;
