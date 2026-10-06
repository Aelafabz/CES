from mrk_reciever import data_path

sales_data = data_path/sales_csv
erca_data =data_path/erca_csv

create_db(){
    if !maraki_db:
        "CREATE DATABASE maraki_db"
        "CREATE table erca"
        "CREATE table sales-report"
}

>>design a schema and relationship between the two tables by associating related/relevant qualities.

populate_db(){
    for s in sales_data.ref_notes:
        if "SEARCH table sales-report for value $s in Ref note" == TRUE:
            skip
            else:
                db.table("sales_report") = sales_data
    for s in erca_data.ref_notes:
        if "SEARCH table erca for value $s in Ref note" == TRUE:
            skip
            else:
                db.table("erca") = erca_data
    }