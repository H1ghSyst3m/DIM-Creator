use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fmt};
use ts_rs::TS;

/// A language-neutral failure. Details are reserved for OS/library diagnostics.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct AppError {
    pub code: String,
    pub params: BTreeMap<String, String>,
    pub details: Option<String>,
    pub causes: Vec<AppError>,
}

impl AppError {
    pub fn new(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            params: BTreeMap::new(),
            details: None,
            causes: Vec::new(),
        }
    }
    pub fn param(mut self, name: &str, value: impl ToString) -> Self {
        self.params.insert(name.into(), value.to_string());
        self
    }
    pub fn diagnostic(code: &str, details: impl fmt::Display) -> Self {
        Self {
            details: Some(details.to_string()),
            ..Self::new(code)
        }
    }
    pub fn caused_by(mut self, causes: impl IntoIterator<Item = AppError>) -> Self {
        self.causes.extend(causes);
        self
    }
}

impl From<&str> for AppError {
    fn from(code: &str) -> Self {
        Self::new(code)
    }
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {:?}", self.code, self.params)?;
        if let Some(details) = &self.details {
            write!(f, ": {details}")?;
        }
        for cause in &self.causes {
            write!(f, "; {cause}")?;
        }
        Ok(())
    }
}
impl std::error::Error for AppError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Error,
    Warning,
}

#[derive(Debug, Clone, Serialize, TS)]
pub struct Issue {
    #[serde(flatten)]
    pub error: AppError,
    pub severity: Severity,
    pub field: String,
}
impl Issue {
    pub fn error(code: &str, field: &str) -> Self {
        Self {
            error: AppError::new(code),
            severity: Severity::Error,
            field: field.into(),
        }
    }
    pub fn cause(code: &str, field: &str, error: AppError) -> Self {
        Self {
            error: AppError::new(code).caused_by([error]),
            ..Self::error(code, field)
        }
    }
    pub fn value(code: &str, field: &str, value: impl ToString) -> Self {
        Self {
            error: AppError::new(code).param("value", value),
            ..Self::error(code, field)
        }
    }
}
