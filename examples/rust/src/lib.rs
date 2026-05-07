pub fn format_status(name: &str, passed: bool) -> String {
    let status = if passed { "passed" } else { "failed" };
    format!("{name}: {status}")
}

#[cfg(test)]
mod tests {
    use super::format_status;

    #[test]
    fn formats_status() {
        assert_eq!(format_status("reward", true), "reward: passed");
    }
}
