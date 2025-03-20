
import React from 'react';
import { ExternalLink } from 'lucide-react';
import maintenanceLinks from '@/data/maintenance-links.json';

const ResourceLinks: React.FC = () => {
  return (
    <div className="pb-4">
      <h3 className="text-sm font-medium mb-4">Resources</h3>
      <div className="space-y-3">
        {maintenanceLinks.map((link, index) => (
          <a 
            key={index}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-start gap-2 text-sm hover:text-primary transition-colors"
          >
            <ExternalLink className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div>
              <div className="font-medium">{link.title}</div>
              <div className="text-xs text-muted-foreground">{link.description}</div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
};

export default ResourceLinks;
