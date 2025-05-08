import React from 'react';
import { Link } from 'react-router-dom';

interface HeaderProps {
  onOpenAboutDialog?: () => void;
}

const Header: React.FC<HeaderProps> = ({ onOpenAboutDialog }) => {
  return (
    <header className="flex justify-between items-center py-4 px-4 sm:px-6 md:px-8 animate-fade-in">
      <Link to="/" className="text-xl font-medium tracking-tight cursor-pointer hover:text-primary transition-colors">
        screen/machine
      </Link>
    </header>
  );
};

export default Header;
