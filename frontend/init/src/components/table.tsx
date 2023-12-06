import React from 'react';
import { Table, TableHead, TableRow, TableCell, TableBody, Button } from '@mui/material';
import Badge from '@mui/material/Badge';
import './table.css'; 
export default function Tabla() {
  return (
    <div className="w-full overflow-auto">
    <div className="full-screen-container">
      <Table className="my-table">
        <TableHead>
          <TableRow>
            <TableCell className="w-[100px]">ID</TableCell>
            <TableCell>Name</TableCell>
            <TableCell>MinPriceWithFee</TableCell>
            <TableCell>MinPriceWithoutFee</TableCell>
            <TableCell>BuyOrderPrice</TableCell>
            <TableCell>LastUpdated</TableCell>
            <TableCell className="text-right">View knife</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          <TableRow className="hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer">
            <TableCell className="font-medium">1</TableCell>
            <TableCell>Knife#1</TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
                Date
            </TableCell>
            <TableCell className="text-right">
              <Button size="small" variant="outlined">
                View
              </Button>
            </TableCell>
          </TableRow>

          <TableRow className="hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer">
            <TableCell className="font-medium">1</TableCell>
            <TableCell>Knife#2</TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
                Date
            </TableCell>
            <TableCell className="text-right">
              <Button size="small" variant="outlined">
                View
              </Button>
            </TableCell>
          </TableRow>

          <TableRow className="hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer">
            <TableCell className="font-medium">1</TableCell>
            <TableCell>Knife#3</TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
              <Badge className="p-1">100</Badge>
            </TableCell>
            <TableCell>
                Date
            </TableCell>
            <TableCell className="text-right">
              <Button size="small" variant="outlined">
                View
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
    </div>
  );
}
