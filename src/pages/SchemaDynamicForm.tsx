import React, { useState, useCallback, useMemo } from 'react';
import { AutoForm, AutoFields, ErrorsField } from 'uniforms-mui';
import { JSONSchemaBridge } from 'uniforms-bridge-json-schema';
import { Box, Button, Typography, Paper, TextField, Alert } from '@mui/material';
import Ajv from 'ajv';

const defaultSchema = {
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Test Schedule Config",
  "type": "object",
  "properties": {
    "triggers": {
      "type": "array",
      "title": "Triggers",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": ["time", "event"],
            "title": "Trigger Type"
          },
          "value": {
            "type": "string",
            "title": "Trigger Value"
          }
        },
        "required": ["type", "value"]
      }
    },
    "instructions": {
      "type": "array",
      "title": "Instructions",
      "items": {
        "type": "object",
        "properties": {
          "action": {
            "type": "string",
            "enum": ["generate", "display"],
            "title": "Action"
          },
          "value": {
            "type": "string",
            "title": "Value"
          }
        },
        "required": ["action"]
      }
    }
  },
  "required": ["triggers", "instructions"],
  "additionalProperties": false
};

const ajv = new Ajv({ 
  allErrors: true, 
  useDefaults: true,
  strict: false
});

function createValidator(schema: any) {
  const validator = ajv.compile(schema);
  return (model: any) => {
    validator(model);
    if (validator.errors?.length) {
      throw { details: validator.errors };
    }
  };
}

const SchemaDynamicForm: React.FC = () => {
  const [schemaText, setSchemaText] = useState(JSON.stringify(defaultSchema, null, 2));
  const [currentSchema, setCurrentSchema] = useState(defaultSchema);
  const [error, setError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleSchemaChange = useCallback((event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setSchemaText(event.target.value);
    try {
      const newSchema = JSON.parse(event.target.value);
      setCurrentSchema(newSchema);
      setError(null);
    } catch (e) {
      setError('Invalid JSON schema');
    }
  }, []);

  const bridge = useMemo(() => {
    try {
      return new JSONSchemaBridge({
        schema: currentSchema,
        validator: createValidator(currentSchema)
      });
    } catch (e) {
      setValidationError('Failed to create form from schema: ' + (e as Error).message);
      return null;
    }
  }, [currentSchema]);

  const handleSubmit = useCallback((model: any) => {
    console.log('Form submitted:', model);
  }, []);

  return (
    <Box sx={{ p: 4 }}>
      <Paper sx={{ p: 3, maxWidth: 800, mx: 'auto' }} elevation={3}>
        <Typography variant="h5" gutterBottom>
          Dynamic Form
        </Typography>
        
        <TextField
          fullWidth
          multiline
          rows={10}
          value={schemaText}
          onChange={handleSchemaChange}
          label="JSON Schema"
          error={!!error}
          helperText={error}
          sx={{ mb: 3 }}
        />

        {validationError && (
          <Alert severity="error" sx={{ mb: 3 }}>
            {validationError}
          </Alert>
        )}

        <Paper sx={{ p: 3, mb: 3 }} elevation={1}>
          <Typography variant="h6" gutterBottom>
            Generated Form
          </Typography>
          {bridge && (
            <AutoForm
              schema={bridge}
              onSubmit={handleSubmit}
            >
              <AutoFields />
              <ErrorsField />
              <Button type="submit" variant="contained" color="primary">
                Submit
              </Button>
            </AutoForm>
          )}
        </Paper>
      </Paper>
    </Box>
  );
};

export default SchemaDynamicForm; 