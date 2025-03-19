
import React from 'react';

const Header = () => {
  return (
    <header className="flex justify-between items-center py-6 px-4 sm:px-6 md:px-8 animate-fade-in">
      <div className="text-2xl font-medium tracking-tight">
        imagine
      </div>
      <div className="flex items-center space-x-4">
        <a href="#" className="text-sm font-medium text-foreground/60 hover:text-foreground transition-colors">
          Gallery
        </a>
        <a href="#" className="text-sm font-medium text-foreground/60 hover:text-foreground transition-colors">
          About
        </a>
      </div>
    </header>
  );
};

export default Header;
