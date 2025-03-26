
import React from 'react';

const Footer: React.FC = () => {
  const currentYear = new Date().getFullYear();
  const version = "1.0.0"; // Version number

  return (
    <footer className="w-full py-4 mt-8 border-t border-gray-100">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
        <p className="text-xs text-center text-muted-foreground">
          © {currentYear} Greg Marsh. v{version}
        </p>
      </div>
    </footer>
  );
};

export default Footer;
