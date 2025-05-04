import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { RefreshCcw, Edit, Trash2 } from 'lucide-react';
import apiService from '@/utils/api';
import { toast } from 'sonner';

interface VarRegistryData {
  global: Record<string, VarInfo>;
  groups: Record<string, Record<string, VarInfo>>;
  imports: Record<string, Record<string, ImportInfo>>;
  last_updated: string;
}

interface VarInfo {
  friendly_name: string;
  owner: string;
  value: any;
  timestamp: string;
}

interface ImportInfo {
  imported_as: string;
  source: string;
  timestamp: string;
}

export const VarsRegistryCard: React.FC = () => {
  const [registryData, setRegistryData] = useState<VarRegistryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string>('global');
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editVar, setEditVar] = useState<{ name: string; value: any } | null>(null);
  
  // Fetch the registry data
  const fetchRegistry = async () => {
    setLoading(true);
    try {
      const response = await apiService.getVarsRegistry();
      if (response && response.status === 'success') {
        setRegistryData(response.registry);
      }
    } catch (error) {
      console.error('Error fetching variables registry:', error);
      toast.error('Failed to fetch variables registry');
    } finally {
      setLoading(false);
    }
  };
  
  // Load registry data on component mount
  useEffect(() => {
    fetchRegistry();
    
    // Set up polling for registry updates (every 30 seconds)
    const intervalId = setInterval(fetchRegistry, 30000);
    
    // Clean up interval on unmount
    return () => clearInterval(intervalId);
  }, []);
  
  // Handle editing a variable
  const handleEditVar = (name: string, value: any) => {
    setEditVar({ name, value });
    setEditDialogOpen(true);
  };
  
  // Handle deleting a variable
  const handleDeleteVar = async (name: string) => {
    if (confirm(`Are you sure you want to delete the variable "${name}"?`)) {
      try {
        await apiService.deleteExportedVar(name);
        toast.success(`Variable "${name}" deleted successfully`);
        fetchRegistry(); // Refresh the registry
      } catch (error) {
        console.error('Error deleting variable:', error);
        toast.error(`Failed to delete variable "${name}"`);
      }
    }
  };
  
  // Handle saving edits to a variable
  const handleSaveEdit = async () => {
    if (!editVar) return;
    
    try {
      await apiService.setExportedVar(editVar.name, editVar.value);
      setEditDialogOpen(false);
      toast.success(`Variable "${editVar.name}" updated successfully`);
      fetchRegistry(); // Refresh the registry
    } catch (error) {
      console.error('Error updating variable:', error);
      toast.error(`Failed to update variable "${editVar.name}"`);
    }
  };
  
  // Render global variables
  const renderGlobalVars = () => {
    if (!registryData || !registryData.global || Object.keys(registryData.global).length === 0) {
      return <p className="text-muted-foreground">No global variables found</p>;
    }
    
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Friendly Name</TableHead>
            <TableHead>Value</TableHead>
            <TableHead className="w-[100px]">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(registryData.global).map(([name, info]) => (
            <TableRow key={name}>
              <TableCell className="font-medium">{name}</TableCell>
              <TableCell>{info.owner}</TableCell>
              <TableCell>{info.friendly_name}</TableCell>
              <TableCell className="max-w-[200px] truncate">
                {typeof info.value === 'object'
                  ? JSON.stringify(info.value)
                  : String(info.value)}
              </TableCell>
              <TableCell>
                <div className="flex space-x-2">
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => handleEditVar(name, info.value)}
                  >
                    <Edit className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => handleDeleteVar(name)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  };
  
  // Render group variables
  const renderGroupVars = () => {
    if (!registryData || !registryData.groups || Object.keys(registryData.groups).length === 0) {
      return <p className="text-muted-foreground">No group variables found</p>;
    }
    
    return (
      <div className="space-y-6">
        {Object.entries(registryData.groups).map(([groupName, groupVars]) => (
          <div key={groupName} className="space-y-2">
            <h3 className="font-medium text-lg flex items-center">
              Group: <Badge className="ml-2">{groupName}</Badge>
            </h3>
            {Object.keys(groupVars).length === 0 ? (
              <p className="text-muted-foreground">No variables in this group</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Owner</TableHead>
                    <TableHead>Friendly Name</TableHead>
                    <TableHead>Value</TableHead>
                    <TableHead className="w-[100px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(groupVars).map(([name, info]) => (
                    <TableRow key={name}>
                      <TableCell className="font-medium">{name}</TableCell>
                      <TableCell>{info.owner}</TableCell>
                      <TableCell>{info.friendly_name}</TableCell>
                      <TableCell className="max-w-[200px] truncate">
                        {typeof info.value === 'object'
                          ? JSON.stringify(info.value)
                          : String(info.value)}
                      </TableCell>
                      <TableCell>
                        <div className="flex space-x-2">
                          <Button
                            variant="outline"
                            size="icon"
                            onClick={() => handleEditVar(name, info.value)}
                          >
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="outline"
                            size="icon"
                            onClick={() => handleDeleteVar(name)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        ))}
      </div>
    );
  };
  
  // Render import relationships
  const renderImports = () => {
    if (!registryData || !registryData.imports || Object.keys(registryData.imports).length === 0) {
      return <p className="text-muted-foreground">No import relationships found</p>;
    }
    
    return (
      <div className="space-y-6">
        {Object.entries(registryData.imports).map(([varName, importers]) => (
          <div key={varName} className="space-y-2">
            <h3 className="font-medium text-lg flex items-center">
              Variable: <Badge variant="outline" className="ml-2">{varName}</Badge>
            </h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Importer</TableHead>
                  <TableHead>Imported As</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Last Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(importers).map(([importerId, info]) => (
                  <TableRow key={importerId}>
                    <TableCell className="font-medium">{importerId}</TableCell>
                    <TableCell>{info.imported_as}</TableCell>
                    <TableCell>{info.source}</TableCell>
                    <TableCell>{new Date(info.timestamp).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ))}
      </div>
    );
  };
  
  return (
    <Card className="w-full">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div>
          <CardTitle>Variables Registry</CardTitle>
          <CardDescription>
            Shared variables between schedulers
            {registryData?.last_updated && (
              <span className="ml-2 text-xs">
                (Last updated: {new Date(registryData.last_updated).toLocaleString()})
              </span>
            )}
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchRegistry}
          disabled={loading}
        >
          <RefreshCcw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="global" value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="mb-4">
            <TabsTrigger value="global">Global</TabsTrigger>
            <TabsTrigger value="groups">Groups</TabsTrigger>
            <TabsTrigger value="imports">Imports</TabsTrigger>
          </TabsList>
          <TabsContent value="global">
            {loading ? <p>Loading global variables...</p> : renderGlobalVars()}
          </TabsContent>
          <TabsContent value="groups">
            {loading ? <p>Loading group variables...</p> : renderGroupVars()}
          </TabsContent>
          <TabsContent value="imports">
            {loading ? <p>Loading import relationships...</p> : renderImports()}
          </TabsContent>
        </Tabs>
      </CardContent>
      
      {/* Edit Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Variable</DialogTitle>
            <DialogDescription>
              Update the value of variable "{editVar?.name}"
            </DialogDescription>
          </DialogHeader>
          
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label htmlFor="value" className="text-sm font-medium">
                Value
              </label>
              <Input
                id="value"
                value={typeof editVar?.value === 'string' ? editVar.value : JSON.stringify(editVar?.value)}
                onChange={(e) => {
                  // Try to parse as JSON if it's a complex value, otherwise use as string
                  let newValue = e.target.value;
                  try {
                    if (e.target.value.startsWith('{') || e.target.value.startsWith('[') || 
                        e.target.value === 'true' || e.target.value === 'false' || 
                        !isNaN(Number(e.target.value))) {
                      newValue = JSON.parse(e.target.value);
                    }
                  } catch (error) {
                    // If parsing fails, use the raw string
                  }
                  setEditVar(prev => prev ? { ...prev, value: newValue } : null);
                }}
              />
            </div>
          </div>
          
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSaveEdit}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}; 