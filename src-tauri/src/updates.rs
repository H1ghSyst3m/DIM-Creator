use dim_creator_core::error::AppError;
use std::time::Duration;

use semver::Version;
use serde::{Deserialize, Serialize};

const REPOSITORY: &str = "H1ghSyst3m/DIM-Creator";

#[derive(Deserialize, ts_rs::TS)]
struct Release {
    tag_name: String,
    draft: bool,
    prerelease: bool,
}

#[derive(Serialize, ts_rs::TS)]
pub struct Update {
    version: String,
    url: String,
}

pub async fn check(current: &Version) -> Result<Option<Update>, AppError> {
    let releases: Vec<Release> = reqwest::Client::builder()
        .user_agent("DIM-Creator")
        .timeout(Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| AppError::diagnostic("updateFailed", error))?
        .get(format!(
            "https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
        ))
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|error| AppError::diagnostic("updateFailed", error))?
        .json()
        .await
        .map_err(|error| AppError::diagnostic("updateFailed", error))?;
    Ok(select(current, releases))
}

fn select(current: &Version, releases: Vec<Release>) -> Option<Update> {
    let (version, tag) = releases
        .into_iter()
        .filter(|release| !release.draft && !release.prerelease)
        .filter_map(|release| {
            let version = Version::parse(release.tag_name.trim_start_matches('v')).ok()?;
            (version.major == current.major && version.pre.is_empty() && &version > current)
                .then_some((version, release.tag_name))
        })
        .max_by(|(first, _), (second, _)| first.cmp(second))?;
    Some(Update {
        version: version.to_string(),
        url: format!("https://github.com/{REPOSITORY}/releases/tag/{tag}"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_the_highest_stable_release_in_the_current_major_version() {
        let releases = ["v2.9.1", "v3.1.0", "v3.3.0-beta.1", "v3.2.0", "v4.0.0"]
            .into_iter()
            .map(|tag| Release {
                tag_name: tag.into(),
                draft: false,
                prerelease: false,
            })
            .collect();
        let update = select(&Version::new(3, 0, 0), releases).unwrap();
        assert_eq!(update.version, "3.2.0");
        assert!(update.url.ends_with("/releases/tag/v3.2.0"));
    }
}
