use aegis_sim::{
    evaluate_backtest, evaluate_dataset, replay_dataset, validate_dataset, Validation,
};
use std::{env, fs, path::Path, process::ExitCode};

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().collect();
    if args.get(1).is_some_and(|arg| arg == "--backtest") {
        if args.len() != 3 {
            eprintln!("Usage: aegis-sim --backtest <evaluation-manifest.json>");
            return ExitCode::from(1);
        }
        let plan_path = Path::new(&args[2]);
        let plan = match fs::read(plan_path) {
            Ok(bytes) => bytes,
            Err(_) => {
                println!(
                    "{{\"status\":\"failed\",\"reason\":\"could not read evaluation manifest\"}}"
                );
                return ExitCode::from(1);
            }
        };
        let root = plan_path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let result = evaluate_backtest(&plan, root);
        println!(
            "{}",
            serde_json::to_string_pretty(&result).expect("JSON report")
        );
        return match result.status.as_str() {
            "ready" => ExitCode::SUCCESS,
            "hold" => ExitCode::from(2),
            _ => ExitCode::from(1),
        };
    }
    let report = args.len() == 4 && args[1] == "--report";
    let replay = args.len() == 4 && args[1] == "--replay";
    if args.len() != 3 && !replay && !report {
        eprintln!("Usage: aegis-sim [--replay|--report] <manifest.json> <data.json>");
        return ExitCode::from(1);
    }
    let offset = if replay || report { 2 } else { 1 };
    let (manifest, data) = match (fs::read(&args[offset]), fs::read(&args[offset + 1])) {
        (Ok(manifest), Ok(data)) => (manifest, data),
        (Err(error), _) => {
            eprintln!("aegis-sim: {}: {error}", args[offset].to_string_lossy());
            println!("FAILED: could not read input files");
            return ExitCode::from(1);
        }
        (_, Err(error)) => {
            eprintln!("aegis-sim: {}: {error}", args[offset + 1].to_string_lossy());
            println!("FAILED: could not read input files");
            return ExitCode::from(1);
        }
    };
    if report {
        let result = evaluate_dataset(&manifest, &data);
        println!(
            "{}",
            serde_json::to_string_pretty(&result).expect("JSON report")
        );
        return match result.status.as_str() {
            "ready" => ExitCode::SUCCESS,
            "hold" => ExitCode::from(2),
            _ => ExitCode::from(1),
        };
    }
    if replay {
        let result = replay_dataset(&manifest, &data);
        println!(
            "{}",
            serde_json::to_string_pretty(&result).expect("report is JSON compatible")
        );
        return match result.status.as_str() {
            "ready" => ExitCode::SUCCESS,
            "hold" => ExitCode::from(2),
            _ => ExitCode::from(1),
        };
    }
    match validate_dataset(&manifest, &data) {
        Validation::Ready => {
            println!("READY: structurally valid input; source completeness is not established");
            ExitCode::SUCCESS
        }
        Validation::Hold(reason) => {
            println!("HOLD: {reason}");
            ExitCode::from(2)
        }
        Validation::Failed(reason) => {
            println!("FAILED: {reason}");
            ExitCode::from(1)
        }
    }
}
