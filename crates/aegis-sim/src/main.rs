use aegis_sim::{replay_dataset, validate_dataset, Validation};
use std::{env, fs, process::ExitCode};

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().collect();
    let replay = args.len() == 4 && args[1] == "--replay";
    if args.len() != 3 && !replay {
        eprintln!("Usage: aegis-sim [--replay] <manifest.json> <data.json>");
        return ExitCode::from(1);
    }
    let offset = if replay { 2 } else { 1 };
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
